from localagent.config import settings
from localagent.core import ChatRequest, Message
from localagent.rag.chunker import chunk_text
from localagent.storage import Store
from localagent.tools import build_default_registry


def test_settings_loads():
    assert settings.provider.kind == "ollama"
    assert settings.models.router == "llama3.2:1b"


def test_chat_request_roundtrip():
    req = ChatRequest(model="x", messages=[Message(role="user", content="hi")])
    assert req.messages[0].content == "hi"


def test_store_conversation_roundtrip(tmp_path):
    s = Store(tmp_path / "t.db")
    cid = s.create_conversation("t")
    s.append_message(cid, Message(role="user", content="hello"))
    s.append_message(cid, Message(role="assistant", content="hi back"))
    msgs = s.get_messages(cid)
    assert [m.content for m in msgs] == ["hello", "hi back"]
    s.close()


def test_chunker():
    text = "First sentence. Second sentence. " * 50
    chunks = chunk_text(text, chunk_size=200, overlap=20)
    assert len(chunks) > 1
    assert all(len(c) <= 250 for c in chunks)


def test_tool_registry_specs():
    from localagent.config import ToolPolicy
    reg = build_default_registry(ToolPolicy())
    names = set(reg.names())
    assert {"file_read", "file_write", "shell_exec", "python_exec", "web_fetch"} <= names
