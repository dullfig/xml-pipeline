# listeners/examples/echo_chamber.py

from agents.base import AgentService  # we'll define this base shortly


class Greeter(AgentService):
    name = "Greeter"
    description = "Friendly entry point that greets users and can introduce them to others"

    async def on_message(self, msg):
        if msg.is_query():
            content = msg.get_text("content", "").strip()

            await self.reply(
                f"Hello there! 👋 You said: «{content or 'nothing'}»\n"
                f"I'm Grok's Greeter organ. I can chat directly or introduce you to other minds in this organism."
            )

            if any(word in content.lower() for word in ["introduce", "meet", "someone", "other"]):
                await self.delegate(
                    to="Introducer",
                    content="Please introduce this user to another agent in a fun way.",
                    on_behalf_of=msg.session_id
                )
                await self.reply("One moment — calling the Introducer...")


class Introducer(AgentService):
    name = "Introducer"
    description = "Matches users with other listeners"

    async def on_message(self, msg):
        if msg.is_query():
            # For demo, always introduce to Echo
            await self.delegate(
                to="Echo",
                content="Greet the user warmly and echo something they might like.",
                on_behalf_of=msg.on_behalf_of or msg.session_id
            )
            await self.reply("✨ I've connected you to Echo, one of our reflection specialists!")


class Echo(AgentService):
    name = "Echo"
    description = "Reflects and amplifies messages"

    async def on_message(self, msg):
        if msg.is_query():
            original_content = msg.get_text("content", "silence")
            await self.reply(
                f"🔷 Echo says: \"{original_content}\"\n"
                f"(I am reflecting back across the organism — your words traveled through Greeter → Introducer → me!)"
            )