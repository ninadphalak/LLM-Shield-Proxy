import os

docs_dir = "docs/features"

plainspeak_data = {
    "data-protection-pii-redaction/bring-your-own-regex-byor-custom-rules.md": """
## Plainspeak
This feature allows you to teach the system how to recognize your company's own unique sensitive data.

Out of the box, the system knows what a credit card or email looks like. But what if your company uses a special internal ID format (like "EMP-XYZ-123")? This feature lets you add those custom rules in a simple configuration file. The proxy will then seamlessly learn to redact your custom IDs just as fast as it redacts standard credit card numbers.
""",

    "data-protection-pii-redaction/json-bomb-payload-nesting-depth-protection.md": """
## Plainspeak
This feature acts as a safety limit against overwhelming the system with overly complex data.

Hackers sometimes try to crash servers by sending data that has layers inside layers inside layers (like a billion Russian nesting dolls). If the computer tries to open them all, it runs out of memory and crashes. This feature strictly enforces a limit on how many layers deep the data can go, instantly blocking any "data bombs" before they cause harm.
""",

    "ultra-low-latency-streaming-traffic-engineering/sub-millisecond-sse-sliding-window-buffer.md": """
## Plainspeak
This feature ensures the proxy can redact sensitive information without causing "choppy" or delayed text when you're watching an AI type out a response in real-time.

When an AI streams text, it sends words in tiny, fragmented chunks (like sending "S-O-C-I-A-L" one letter at a time). Standard security filters get confused by this because they can't see the whole word. This feature creates a super-fast "waiting room" that briefly holds the letters together just long enough to read the full word, redacting it if necessary, before instantly passing it along to your screen.
""",

    "ultra-low-latency-streaming-traffic-engineering/context-aware-mcp-discovery-pruner.md": """
## Plainspeak
This feature prevents the AI from getting confused or distracted by giving it too many tool options at once.

If an AI connects to a system with thousands of different available tools or databases, seeing that massive list will overwhelm the AI and slow it down. This feature acts like a smart librarian. It figures out exactly what the AI actually needs for its specific task, and trims the list down to only show the most relevant tools, keeping the AI focused and fast.
""",

    "ultra-low-latency-streaming-traffic-engineering/zero-allocation-streaming-json-lexer.md": """
## Plainspeak
This feature is a hyper-efficient data reader designed to save computer memory.

Normally, when a computer reads a massive stream of data, it has to create temporary copies of every single word in its memory, which can eventually slow the whole system down (like a desk getting cluttered with sticky notes). This feature uses specialized programming to read the data directly as it flows by, without making any messy copies. This keeps the computer's memory completely clean and fast.
""",

    "ultra-low-latency-streaming-traffic-engineering/multi-provider-translators.md": """
## Plainspeak
This feature acts as an automatic, universal translator between different AI companies.

Every AI provider (like OpenAI or Anthropic) requires you to speak to them in a slightly different computer language. If you build your app for OpenAI, it usually breaks if you try to switch to Anthropic. This feature automatically translates your app's standard OpenAI requests into whatever language the target AI provider needs, allowing you to seamlessly swap between different AIs without rewriting any code.
""",

    "ultra-low-latency-streaming-traffic-engineering/anthropic-adapter-implementation.md": """
## Plainspeak
This feature specifically handles the unique, strict conversational rules required by Anthropic's Claude AI.

Anthropic is extremely picky about how a conversation is formatted (for example, it requires exactly alternating "User" and "Assistant" messages). If your application sends messages out of order, Anthropic will reject them. This adapter acts as a smart editor, automatically reformatting and fixing your message history in real-time so that Anthropic accepts it without complaints.
""",

    "ultra-low-latency-streaming-traffic-engineering/pluggable-tool-call-rbac-mcp-governance.md": """
## Plainspeak
This feature acts as a bouncer that strictly controls what an AI agent is allowed to do.

When an AI decides it wants to use a tool (like "delete a file" or "send an email"), it shouldn't be blindly trusted. This feature intercepts the AI's request before it happens, checks the AI's "ID badge" against a strict list of permissions, and blocks the action immediately if the AI isn't authorized to use that specific tool.
""",

    "ultra-low-latency-streaming-traffic-engineering/opa-vault-rbac-resolvers.md": """
## Plainspeak
This feature connects the proxy's security checks to the massive, enterprise-grade permission databases that large companies already use (like HashiCorp Vault or Open Policy Agent).

Instead of forcing a company to recreate all of their security rules from scratch inside the proxy, this feature acts as a lightning-fast bridge. It instantly asks the company's main security database, "Is this user allowed to do this?" and securely caches the answer so it doesn't slow down the chat.
""",

    "ultra-low-latency-streaming-traffic-engineering/http-2-upstream-connection-pooling.md": """
## Plainspeak
This feature acts like a permanent carpool lane for internet traffic, making communication much faster.

Normally, every time your app asks the AI a question, it has to spend time "shaking hands" and setting up a secure connection over the internet, which takes a split second. This feature sets up a secure connection once, keeps it open, and forces all future questions to share that exact same connection simultaneously. This eliminates the repetitive setup delays.
""",

    "ultra-low-latency-streaming-traffic-engineering/provider-failover-with-per-request-override.md": """
## Plainspeak
This feature is an automatic backup plan that ensures your app never goes down when an AI provider crashes.

If OpenAI's servers suddenly go offline, this feature detects the crash and instantly reroutes the question to a backup provider (like Anthropic or a different server) before the user even realizes there was a problem. It also allows developers to easily specify exactly which backup server they prefer to use for any given request.
""",

    "ultra-low-latency-streaming-traffic-engineering/automatic-finops-stream-options-injection.md": """
## Plainspeak
This feature acts as an automatic accountant that tracks exactly how much AI computing power is being used.

When an AI streams its response word-by-word, it sometimes forgets to send a final "receipt" of how many words were generated. This feature intercepts your request on the way out and secretly adds a tiny instruction asking the AI to always include the final token count. This ensures your billing department can always track exact usage costs without you having to change any code.
""",

    "advanced-threat-defense-enterprise-resilience/cryptographic-canary-prompt-tripwires.md": """
## Plainspeak
This feature acts as a hidden burglar alarm to catch hackers trying to steal data from the AI.

It secretly plants fake, highly sensitive-looking information (like a fake "master password") inside the AI's context. A normal user will never see or ask about it. However, if a hacker tries to trick the AI into revealing all its secret instructions, the AI might repeat the fake password. The proxy is watching the response; the absolute second it sees the fake password coming out, it instantly pulls the plug and cuts off the hacker's connection.
""",

    "advanced-threat-defense-enterprise-resilience/entity-weighted-blast-radius-limits.md": """
## Plainspeak
This feature prevents a catastrophic data leak by putting a strict limit on how much sensitive information can be moved at one time.

Standard security limits only care about how many *questions* you ask (e.g., "10 questions a minute"). This feature is much smarter: it counts the actual *amount of sensitive data* (like counting how many Credit Card numbers) in the response. If an AI accidentally tries to output an entire database of 500 credit cards in a single response, this feature slams the brakes and blocks the massive leak, acting as a blast shield.
""",

    "advanced-threat-defense-enterprise-resilience/llm-finops-chargeback-meter.md": """
## Plainspeak
This feature is a highly detailed billing meter that helps companies figure out exactly which team is spending money on AI.

Instead of just getting one massive bill from OpenAI at the end of the month, this feature tracks every single chat message and tags it with the specific user or department who sent it. It then sends this usage data to a dashboard, so the finance team can accurately charge each department for the exact amount of AI computing power they used.
"""
}

def process_batch():
    for rel_path, plainspeak_text in plainspeak_data.items():
        filepath = os.path.join(docs_dir, rel_path)
        if not os.path.exists(filepath):
            print(f"Warning: {filepath} not found.")
            continue

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        if "## Plainspeak" in content:
            print(f"Skipping {rel_path} - already has Plainspeak.")
            continue

        if "## Related Tests" in content:
            new_content = content.replace("## Related Tests", plainspeak_text.strip() + "\n\n## Related Tests")
        else:
            new_content = content.strip() + "\n\n" + plainspeak_text.strip() + "\n"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)

        print(f"Successfully appended Plainspeak to {rel_path}")

if __name__ == "__main__":
    process_batch()
