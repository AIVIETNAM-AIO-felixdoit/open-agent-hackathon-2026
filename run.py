"""CLI: python run.py --input input_examples/example_1.json [--sample]"""
import argparse
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

from agent import investigate


async def main(path: str, sample: bool):
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    async for event in investigate(payload["company"], payload.get("question", ""), sample=sample):
        if event["type"] == "trace":
            extra = {k: v for k, v in event.items() if k not in ("type", "t", "agent", "to", "action")}
            arrow = f" -> {event['to']}" if event["to"] else ""
            print(f"[{event['t']}] {event['agent']}{arrow}: {event['action']} {json.dumps(extra, ensure_ascii=False)[:200]}")
        elif event["type"] == "result":
            print("\n=== RESULT ===")
            print(json.dumps(event["memo"], indent=2, ensure_ascii=False))
            if not sample:
                print(f"\nFull trace: logs/trace_{event['run_id']}.jsonl (H-MEM ledger and retrievals alongside)")
        else:
            print("ERROR:", event["message"], file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--sample", action="store_true", help="replay a recorded run from samples/ (no keys needed)")
    args = parser.parse_args()
    asyncio.run(main(args.input, args.sample))
