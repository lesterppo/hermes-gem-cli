"""Deep diagnosis (scratch, not shipped): why --list-chats is empty and why
deep-research plan creation returns empty, using the repo's own client/RPC
helpers. Prints structure only — no cookie values, no response bodies."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # repo root
spec = importlib.util.spec_from_file_location("gemini", ROOT / "gemini.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


async def main():
    sid, ts = g.resolve_auth()
    print("auth src:", g._LAST_AUTH_SRC, "| sid prefix:", (sid or "")[:12], "| has_ts:", bool(ts))
    client = g.GeminiClient(secure_1psid=sid, secure_1psidts=ts)
    await client.init()
    print("client init ok; models:", [str(m) for m in client.list_models()])

    # 1. list-chats: library path vs MaZiqc RPC path
    try:
        chats = await client.list_chats()
        print("library list_chats:", type(chats).__name__,
              "len:", len(chats) if hasattr(chats, "__len__") else "?")
    except Exception as e:
        print("library list_chats ERROR:", type(e).__name__, str(e)[:200])
    try:
        rpc = await g.BrowserBatchRPC(sid, ts).init()
        try:
            text = await rpc.rpc("MaZiqc", [20, None, [0, None, 1]])
            print("MaZiqc raw chars:", len(text))
            payloads = g._extract_rpc_payloads(text, "MaZiqc")
            print("MaZiqc payloads:", len(payloads))
            n_chat = 0
            for pb in payloads:
                if isinstance(pb, list) and len(pb) > 2 and isinstance(pb[2], list):
                    for cd in pb[2]:
                        if isinstance(cd, list) and cd and isinstance(cd[0], str) and cd[0].startswith("c_"):
                            n_chat += 1
            print("MaZiqc chats found:", n_chat)
            if payloads:
                print("first payload shape:", json.dumps(payloads[0])[:300])
        finally:
            await rpc.close()
    except Exception as e:
        print("MaZiqc RPC ERROR:", type(e).__name__, str(e)[:300])

    # 2. deep-research plan creation
    try:
        plan = await asyncio.wait_for(
            client.create_deep_research_plan("What is Gemini 4 Hydrogen?", model=None),
            timeout=120)
        print("plan type:", type(plan).__name__)
        print("plan title:", getattr(plan, "title", None))
        print("plan attrs:", [a for a in dir(plan) if not a.startswith("_")][:20])
    except Exception as e:
        print("create_deep_research_plan ERROR:", type(e).__name__, str(e)[:500])


if __name__ == "__main__":
    asyncio.run(main())
