import re
import json

file_path = "/home/chr/.gemini/antigravity/brain/ca561156-20e4-4ba3-a6f6-56cb0b114e66/.system_generated/steps/615/content.md"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

print("File Length:", len(content))

# Look for standard method paths like "/msg/..." or similar
paths = re.findall(r"\"/msg/[a-zA-Z0-9_]+\"", content)
print("Found paths:", set(paths))

# Search for fields inside the large serialized state with escaped quotes
print("\n--- Searching for API detail parameters ---")
keys = ["method", "sync", "limit", "pageSize", "cursor", "startTime", "endTime", "direction", "toId"]
for key in keys:
    matches = [m.start() for m in re.finditer(rf'{key}', content)]
    print(f"Key '{key}': found {len(matches)} matches")
    for idx, m_start in enumerate(matches[:2]):
        start = max(0, m_start - 100)
        end = min(len(content), m_start + 400)
        print(f"  Match {idx+1}:")
        print(content[start:end])
        print("  " + "-" * 50)

