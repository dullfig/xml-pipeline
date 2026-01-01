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
│   ├── prompts/
│   │   ├── grok_classic.py
│   │   └── no_paperclippers.py
│   ├── schema/
│   │   ├── payloads/
│   │   │   └── grok-response.xsd
│   │   ├── envelope.xsd
│   │   └── privileged-msg.xsd
│   ├── utils/
│   │   ├── __init__.py
│   │   └── message.py
│   ├── __init__.py
│   ├── agentserver.py
│   ├── main.py
│   ├── message_bus.py
│   └── xml_listener.py
├── docs/
│   ├── AgentServer.md
│   ├── Local Privilege only.md
│   ├── logic and iteration.md
│   └── prompt-no-paperclippers.md
├── scripts/
│   └── generate_organism_key.py
├── tests/
│   └── __init__.py
├── xml_pipeline.egg-info/
│   ├── PKG-INFO
│   ├── SOURCES.txt
│   ├── dependency_links.txt
│   ├── requires.txt
│   └── top_level.txt
├── LICENSE
├── README.md
├── README.v0.md
├── README.v1.md
├── __init__.py
├── pyproject.toml
├── setup-project.ps1
└── structure.md

```
