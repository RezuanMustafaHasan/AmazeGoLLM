"""Reference API client: replace the safe-arrow policy with your LLM decision call."""

import argparse
import json
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--level", default="level-001")
    args = parser.parse_args()
    base = args.base_url.rstrip("/") + "/api/v1"
    token = None

    def request(path, body=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        with urlopen(Request(base + path, data=data, headers=headers), timeout=30) as response:
            return json.load(response)

    token = request("/players", {})["access_token"]
    state = request(
        "/sessions",
        {
            "level_id": args.level,
            "actor_type": "agent",
            "actor_name": "reference-safe-arrow-policy",
        },
    )
    while state["status"] == "active":
        # Your LLM can instead read state["matrix"] and state["arrows"] to pick an ID.
        arrow_id = state["legal_arrow_ids"][0]
        result = request(
            f"/sessions/{state['session_id']}/actions",
            {
                "arrow_id": arrow_id,
                "expected_revision": state["revision"],
                "action_id": str(uuid4()),
            },
        )
        state = result["state"]
        print(
            f"Arrow {arrow_id}: {result['outcome']['result']}; "
            f"{state['remaining_count']} remaining, {state['lives_remaining']} lives"
        )
    print(f"Finished: {state['status']} in {state['moves']} moves.")


if __name__ == "__main__":
    main()
