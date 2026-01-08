```
xml-pipeline/
├── agentserver/
│   ├── auth/
│   │   ├── __init__.py
│   │   └── totp.py
│   ├── config/
│   │   ├── organism_identity/
│   │   │   └── README.txt
│   │   └── __init__.py
│   ├── listeners/
│   │   ├── examples/
│   │   │   ├── __init__.py
│   │   │   ├── echo_chamber.py
│   │   │   └── grok_personality.py
│   │   ├── __init__.py
│   │   ├── llm_connection.py
│   │   └── llm_listener.py
│   ├── message_bus/
│   │   ├── steps/
│   │   │   ├── __init__.py
│   │   │   ├── c14n.py
│   │   │   ├── deserialization.py
│   │   │   ├── envelope_validation.py
│   │   │   ├── payload_extraction.py
│   │   │   ├── repair.py
│   │   │   ├── routing_resolution.py
│   │   │   ├── test_c14n.py
│   │   │   ├── test_repair.py
│   │   │   ├── thread_assignment.py
│   │   │   └── xsd_validation.py
│   │   ├── __init__.py
│   │   ├── bus.py
│   │   ├── config.py
│   │   ├── envelope.py
│   │   ├── errors.py
│   │   ├── message_state.py
│   │   ├── scheduler.py
│   │   └── thread.py
│   ├── prompts/
│   │   ├── grok_classic.py
│   │   └── no_paperclippers.py
│   ├── schema/
│   │   ├── envelope.xsd
│   │   └── privileged-msg.xsd
│   ├── utils/
│   │   ├── __init__.py
│   │   └── message.py
│   ├── __init__.py
│   ├── agentserver.py
│   ├── main.py
│   └── xml_listener.py
├── docs/
│   ├── archive-obsolete/
│   │   ├── logic-and-iteration.md
│   │   ├── thread-management.md
│   │   └── token-scheduling-issues.md
│   ├── configuration.md
│   ├── core-principles-v2.1.md
│   ├── doc_cross_check.md
│   ├── handler-contract-v2.1.md
│   ├── listener-class-v2.1.md
│   ├── message-pump-v2.1.md
│   ├── primitives.md
│   ├── self-grammar-generation.md
│   └── why-not-json.md
├── tests/
│   ├── scripts/
│   │   └── generate_organism_key.py
│   └── __init__.py
├── third_party/
│   └── xmlable/
│       ├── __init__.py
│       ├── _errors.py
│       ├── _io.py
│       ├── _lxml_helpers.py
│       ├── _manual.py
│       ├── _user.py
│       ├── _utils.py
│       ├── _xmlify.py
│       ├── _xobject.py
│       └── py.typed
├── xml_pipeline.egg-info/
│   ├── PKG-INFO
│   ├── SOURCES.txt
│   ├── dependency_links.txt
│   ├── requires.txt
│   └── top_level.txt
├── LICENSE
├── README.md
├── __init__.py
├── pyproject.toml
├── setup-project.ps1
└── structure.md
```