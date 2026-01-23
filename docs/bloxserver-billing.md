# BloxServer Billing Integration — Stripe

**Status:** Design
**Date:** January 2026

## Overview

BloxServer uses Stripe for subscription management, usage-based billing, and payment processing. This document specifies the integration architecture, webhook handlers, and usage tracking system.

## Pricing Tiers

| Tier | Price | Runs/Month | Features |
|------|-------|------------|----------|
| **Free** | $0 | 1,000 | 1 workflow, built-in tools, community support |
| **Pro** | $29 | 100,000 | Unlimited workflows, marketplace, WASM, project memory, priority support |
| **Enterprise** | Custom | Unlimited | SSO/SAML, SLA, dedicated support, private marketplace |

### Overage Pricing (Pro)

| Metric | Included | Overage Rate |
|--------|----------|--------------|
| Workflow runs | 100K/mo | $0.50 per 1K |
| Storage | 10 GB | $0.10 per GB |
| WASM execution | 1000 CPU-sec | $0.01 per CPU-sec |

## Stripe Product Structure

```
Products:
├── bloxserver_free
│   └── price_free_monthly ($0/month, metered runs)
├── bloxserver_pro
│   ├── price_pro_monthly ($29/month base)
│   ├── price_pro_runs_overage (metered, $0.50/1K)
│   └── price_pro_storage_overage (metered, $0.10/GB)
└── bloxserver_enterprise
    └── price_enterprise_custom (quoted per customer)
```

### Stripe Configuration

```python
# One-time setup (or via Stripe Dashboard)

# Free tier product
free_product = stripe.Product.create(
    name="BloxServer Free",
    description="Build AI agent swarms, visually",
)

free_price = stripe.Price.create(
    product=free_product.id,
    unit_amount=0,
    currency="usd",
    recurring={"interval": "month"},
    metadata={"tier": "free", "runs_included": "1000"}
)

# Pro tier product
pro_product = stripe.Product.create(
    name="BloxServer Pro",
    description="Unlimited workflows, marketplace access, custom WASM",
)

pro_base_price = stripe.Price.create(
    product=pro_product.id,
    unit_amount=2900,  # $29.00
    currency="usd",
    recurring={"interval": "month"},
    metadata={"tier": "pro", "runs_included": "100000"}
)

pro_runs_overage = stripe.Price.create(
    product=pro_product.id,
    currency="usd",
    recurring={
        "interval": "month",
        "usage_type": "metered",
        "aggregate_usage": "sum",
    },
    unit_amount_decimal="0.05",  # $0.0005 per run = $0.50 per 1K
    metadata={"type": "runs_overage"}
)
```

## Database Schema

```sql
-- Users table (synced from Clerk + Stripe)
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clerk_id VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) NOT NULL,
    name VARCHAR(255),

    -- Stripe fields
    stripe_customer_id VARCHAR(255) UNIQUE,
    stripe_subscription_id VARCHAR(255),
    stripe_subscription_item_id VARCHAR(255),  -- For usage reporting

    -- Billing state (cached from Stripe)
    tier VARCHAR(50) DEFAULT 'free',  -- free, pro, enterprise
    billing_status VARCHAR(50) DEFAULT 'active',  -- active, past_due, canceled
    trial_ends_at TIMESTAMPTZ,
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Usage tracking (local, for dashboard + Stripe sync)
CREATE TABLE usage_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    period_start DATE NOT NULL,  -- Billing period start

    -- Metrics
    workflow_runs INT DEFAULT 0,
    llm_tokens_in INT DEFAULT 0,
    llm_tokens_out INT DEFAULT 0,
    wasm_cpu_seconds DECIMAL(10,2) DEFAULT 0,
    storage_gb_hours DECIMAL(10,2) DEFAULT 0,

    -- Stripe sync state
    last_synced_at TIMESTAMPTZ,
    last_synced_runs INT DEFAULT 0,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(user_id, period_start)
);

-- Stripe webhook events (idempotency)
CREATE TABLE stripe_events (
    event_id VARCHAR(255) PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    processed_at TIMESTAMPTZ DEFAULT NOW(),
    payload JSONB
);

-- Index for cleanup
CREATE INDEX idx_stripe_events_processed ON stripe_events(processed_at);
```

## Usage Tracking

### Real-Time Counting (Redis)

```python
# On every workflow execution
async def record_workflow_run(user_id: str):
    """Increment run counter in Redis."""
    key = f"usage:{user_id}:runs:{get_current_period()}"
    await redis.incr(key)
    await redis.expire(key, 86400 * 35)  # 35 days TTL

    # Track users with usage for batch sync
    await redis.sadd("users:with_usage", user_id)

async def record_llm_tokens(user_id: str, tokens_in: int, tokens_out: int):
    """Track LLM token usage."""
    period = get_current_period()
    await redis.incrby(f"usage:{user_id}:tokens_in:{period}", tokens_in)
    await redis.incrby(f"usage:{user_id}:tokens_out:{period}", tokens_out)
```

### Periodic Sync to Stripe (Hourly)

```python
async def sync_usage_to_stripe():
    """Hourly job: push usage increments to Stripe."""

    user_ids = await redis.smembers("users:with_usage")

    for user_id in user_ids:
        user = await get_user(user_id)
        if not user.stripe_subscription_item_id:
            continue  # Free tier without Stripe subscription

        # Get usage since last sync
        period = get_current_period()
        runs_key = f"usage:{user_id}:runs:{period}"

        current_runs = int(await redis.get(runs_key) or 0)
        last_synced = await get_last_synced_runs(user_id, period)

        delta = current_runs - last_synced
        if delta <= 0:
            continue

        # Check if over included limit
        tier_limit = get_tier_runs_limit(user.tier)  # 1000 or 100000
        if current_runs <= tier_limit:
            # Still within included runs, just track locally
            await update_last_synced(user_id, period, current_runs)
            continue

        # Calculate overage to report
        overage_start = max(last_synced, tier_limit)
        overage_runs = current_runs - overage_start

        if overage_runs > 0:
            # Report to Stripe
            await stripe.subscription_items.create_usage_record(
                user.stripe_subscription_item_id,
                quantity=overage_runs,
                timestamp=int(time.time()),
                action='increment'
            )

        await update_last_synced(user_id, period, current_runs)

    # Clear the tracking set (will rebuild next hour)
    await redis.delete("users:with_usage")
```

### Dashboard Query

```python
async def get_usage_dashboard(user_id: str) -> UsageDashboard:
    """Get current usage for user dashboard."""
    user = await get_user(user_id)
    period = get_current_period()

    # Get real-time counts from Redis
    runs = int(await redis.get(f"usage:{user_id}:runs:{period}") or 0)
    tokens_in = int(await redis.get(f"usage:{user_id}:tokens_in:{period}") or 0)
    tokens_out = int(await redis.get(f"usage:{user_id}:tokens_out:{period}") or 0)

    tier_limit = get_tier_runs_limit(user.tier)

    return UsageDashboard(
        period_start=period,
        period_end=user.current_period_end,

        runs_used=runs,
        runs_limit=tier_limit,
        runs_percentage=min(100, (runs / tier_limit) * 100),

        tokens_used=tokens_in + tokens_out,

        estimated_overage=calculate_overage_cost(runs, tier_limit),

        days_remaining=(user.current_period_end - datetime.now()).days,
    )
```

## Subscription Lifecycle

### Signup Flow

```
User clicks "Start Free Trial"
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ 1. Create Stripe Customer                                  │
│                                                            │
│    customer = stripe.Customer.create(                      │
│        email=user.email,                                   │
│        metadata={"clerk_id": user.clerk_id}                │
│    )                                                       │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ 2. Create Checkout Session (hosted payment page)           │
│                                                            │
│    session = stripe.checkout.Session.create(               │
│        customer=customer.id,                               │
│        mode='subscription',                                │
│        line_items=[{                                       │
│            'price': 'price_pro_monthly',                   │
│            'quantity': 1                                   │
│        }, {                                                │
│            'price': 'price_pro_runs_overage',  # metered   │
│        }],                                                 │
│        subscription_data={                                 │
│            'trial_period_days': 14,                        │
│        },                                                  │
│        success_url='https://app.openblox.ai/welcome',      │
│        cancel_url='https://app.openblox.ai/pricing',       │
│    )                                                       │
│                                                            │
│    → Redirect user to session.url                          │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ 3. User enters payment details on Stripe Checkout          │
│                                                            │
│    Card validated but NOT charged (trial)                  │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ 4. Webhook: checkout.session.completed                     │
│                                                            │
│    → Update user with stripe_customer_id                   │
│    → Update user with stripe_subscription_id               │
│    → Set tier = 'pro'                                      │
│    → Set trial_ends_at                                     │
└───────────────────────────────────────────────────────────┘
```

### Trial End

```
Day 11 of 14-day trial
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ Scheduled job: Trial ending soon emails                    │
│                                                            │
│ SELECT * FROM users                                        │
│ WHERE trial_ends_at BETWEEN NOW() AND NOW() + INTERVAL '3d'│
│ AND billing_status = 'trialing'                            │
│                                                            │
│ → Send "Your trial ends in 3 days" email                   │
└───────────────────────────────────────────────────────────┘
        │
        ▼
Day 14: Trial ends
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ Stripe automatically:                                      │
│ 1. Charges the card on file                               │
│ 2. Sends invoice.payment_succeeded webhook                │
│                                                            │
│ Our webhook handler:                                       │
│ → Update billing_status = 'active'                        │
│ → Send "Welcome to Pro!" email                            │
└───────────────────────────────────────────────────────────┘
```

### Cancellation

```python
# User clicks "Cancel subscription" in Customer Portal
# Stripe sends webhook

@webhook("customer.subscription.updated")
async def handle_subscription_updated(event):
    subscription = event.data.object
    user = await get_user_by_stripe_subscription(subscription.id)

    if subscription.cancel_at_period_end:
        # User requested cancellation (takes effect at period end)
        await send_email(user, "subscription_canceled", {
            "effective_date": subscription.current_period_end
        })
        await db.execute("""
            UPDATE users
            SET billing_status = 'canceling',
                updated_at = NOW()
            WHERE id = $1
        """, user.id)

@webhook("customer.subscription.deleted")
async def handle_subscription_deleted(event):
    subscription = event.data.object
    user = await get_user_by_stripe_subscription(subscription.id)

    # Subscription actually ended
    await db.execute("""
        UPDATE users
        SET tier = 'free',
            billing_status = 'canceled',
            stripe_subscription_id = NULL,
            stripe_subscription_item_id = NULL,
            updated_at = NOW()
        WHERE id = $1
    """, user.id)

    await send_email(user, "downgraded_to_free")
```

## Webhook Handlers

### Endpoint Setup

```python
from fastapi import FastAPI, Request, HTTPException
import stripe

app = FastAPI()

@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(400, "Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(400, "Invalid signature")

    # Idempotency check
    if await is_event_processed(event.id):
        return {"status": "already_processed"}

    # Route to handler
    handler = WEBHOOK_HANDLERS.get(event.type)
    if handler:
        await handler(event)
    else:
        logger.info(f"Unhandled webhook: {event.type}")

    # Mark processed
    await mark_event_processed(event)

    return {"status": "success"}
```

### Handler Registry

```python
WEBHOOK_HANDLERS = {
    # Checkout
    "checkout.session.completed": handle_checkout_completed,

    # Subscriptions
    "customer.subscription.created": handle_subscription_created,
    "customer.subscription.updated": handle_subscription_updated,
    "customer.subscription.deleted": handle_subscription_deleted,
    "customer.subscription.trial_will_end": handle_trial_ending,

    # Payments
    "invoice.payment_succeeded": handle_payment_succeeded,
    "invoice.payment_failed": handle_payment_failed,
    "invoice.upcoming": handle_invoice_upcoming,

    # Customer
    "customer.updated": handle_customer_updated,
}
```

### Key Handlers

```python
@webhook("checkout.session.completed")
async def handle_checkout_completed(event):
    """User completed checkout - provision their account."""
    session = event.data.object

    # Get or create user
    user = await get_user_by_clerk_id(session.client_reference_id)

    # Update with Stripe IDs
    subscription = await stripe.Subscription.retrieve(session.subscription)

    await db.execute("""
        UPDATE users SET
            stripe_customer_id = $1,
            stripe_subscription_id = $2,
            stripe_subscription_item_id = $3,
            tier = $4,
            billing_status = $5,
            trial_ends_at = $6,
            current_period_start = $7,
            current_period_end = $8,
            updated_at = NOW()
        WHERE id = $9
    """,
        session.customer,
        subscription.id,
        subscription['items'].data[0].id,  # First item for usage reporting
        'pro',
        subscription.status,  # 'trialing' or 'active'
        datetime.fromtimestamp(subscription.trial_end) if subscription.trial_end else None,
        datetime.fromtimestamp(subscription.current_period_start),
        datetime.fromtimestamp(subscription.current_period_end),
        user.id
    )


@webhook("invoice.payment_failed")
async def handle_payment_failed(event):
    """Payment failed - notify user, potentially downgrade."""
    invoice = event.data.object
    user = await get_user_by_stripe_customer(invoice.customer)

    attempt_count = invoice.attempt_count

    if attempt_count == 1:
        # First failure - soft warning
        await send_email(user, "payment_failed_soft", {
            "amount": invoice.amount_due / 100,
            "update_url": await get_customer_portal_url(user)
        })

    elif attempt_count == 2:
        # Second failure - stronger warning
        await send_email(user, "payment_failed_warning", {
            "amount": invoice.amount_due / 100,
            "days_until_downgrade": 3
        })

    else:
        # Final failure - downgrade
        await db.execute("""
            UPDATE users SET
                tier = 'free',
                billing_status = 'past_due',
                updated_at = NOW()
            WHERE id = $1
        """, user.id)

        await send_email(user, "downgraded_payment_failed")


@webhook("customer.subscription.trial_will_end")
async def handle_trial_ending(event):
    """Trial ending in 3 days - Stripe sends this automatically."""
    subscription = event.data.object
    user = await get_user_by_stripe_subscription(subscription.id)

    await send_email(user, "trial_ending", {
        "trial_end_date": datetime.fromtimestamp(subscription.trial_end),
        "amount": 29.00,  # Pro price
        "manage_url": await get_customer_portal_url(user)
    })
```

## Customer Portal

Stripe's hosted portal for self-service billing management.

```python
async def get_customer_portal_url(user: User) -> str:
    """Generate a portal session URL for the user."""
    session = await stripe.billing_portal.Session.create(
        customer=user.stripe_customer_id,
        return_url="https://app.openblox.ai/settings/billing"
    )
    return session.url
```

**Portal capabilities:**
- Update payment method
- View invoices and receipts
- Cancel subscription
- Upgrade/downgrade plan (if configured)

## Email Templates

| Trigger | Template | Content |
|---------|----------|---------|
| Trial started | `trial_started` | Welcome, trial ends on X |
| Trial ending (3 days) | `trial_ending` | Your trial ends soon, card will be charged |
| Trial converted | `trial_converted` | Welcome to Pro! |
| Payment succeeded | `payment_succeeded` | Receipt attached |
| Payment failed (1st) | `payment_failed_soft` | Please update your card |
| Payment failed (2nd) | `payment_failed_warning` | Service will be interrupted |
| Payment failed (final) | `downgraded_payment_failed` | You've been downgraded |
| Subscription canceled | `subscription_canceled` | Access until period end |
| Downgraded | `downgraded_to_free` | You're now on Free |

## Rate Limiting & Abuse Prevention

### Soft Limits (Warning)

```python
async def check_usage_limits(user_id: str) -> UsageLimitResult:
    """Check if user is approaching limits."""
    usage = await get_current_usage(user_id)
    user = await get_user(user_id)
    tier_limit = get_tier_runs_limit(user.tier)

    percentage = (usage.runs / tier_limit) * 100

    if percentage >= 100:
        return UsageLimitResult(
            allowed=True,  # Still allow, but warn
            warning="You've exceeded your included runs. Overage charges apply.",
            overage_rate="$0.50 per 1,000 runs"
        )
    elif percentage >= 80:
        return UsageLimitResult(
            allowed=True,
            warning=f"You've used {percentage:.0f}% of your monthly runs."
        )

    return UsageLimitResult(allowed=True)
```

### Hard Limits (Free Tier)

```python
async def enforce_free_tier_limits(user_id: str) -> bool:
    """Free tier has hard limits - no overage allowed."""
    user = await get_user(user_id)
    if user.tier != "free":
        return True  # Paid tiers have soft limits

    usage = await get_current_usage(user_id)
    if usage.runs >= 1000:
        raise UsageLimitExceeded(
            "You've reached the Free tier limit of 1,000 runs/month. "
            "Upgrade to Pro for unlimited workflows."
        )

    return True
```

## Testing

### Test Mode

Stripe provides test mode with test API keys and test card numbers.

```python
# .env
STRIPE_SECRET_KEY=sk_test_...  # Test mode
STRIPE_WEBHOOK_SECRET=whsec_...

# Test cards
# 4242424242424242 - Succeeds
# 4000000000000002 - Declined
# 4000002500003155 - Requires 3D Secure
```

### Webhook Testing

```bash
# Use Stripe CLI to forward webhooks locally
stripe listen --forward-to localhost:8000/webhooks/stripe

# Trigger test events
stripe trigger invoice.payment_succeeded
stripe trigger customer.subscription.trial_will_end
```

## Monitoring & Alerts

| Metric | Alert Threshold |
|--------|-----------------|
| Webhook processing time | > 5 seconds |
| Webhook failure rate | > 1% |
| Payment failure rate | > 5% |
| Usage sync lag | > 2 hours |
| Stripe API errors | Any 5xx |

## Security Checklist

- [ ] Webhook signature verification
- [ ] Idempotent event processing
- [ ] API keys in environment variables (never in code)
- [ ] Customer portal for sensitive operations (not custom UI)
- [ ] PCI compliance via Stripe Checkout (no card data touches our servers)
- [ ] Audit log for billing events

---

## References

- [Stripe Billing](https://stripe.com/docs/billing)
- [Stripe Webhooks](https://stripe.com/docs/webhooks)
- [Stripe Checkout](https://stripe.com/docs/payments/checkout)
- [Stripe Customer Portal](https://stripe.com/docs/billing/subscriptions/customer-portal)
- [Metered Billing](https://stripe.com/docs/billing/subscriptions/metered-billing)
