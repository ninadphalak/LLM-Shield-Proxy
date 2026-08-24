import os

docs_dir = "docs/features"

plainspeak_data = {
    "advanced-threat-defense-enterprise-resilience/provider-failover-routing.md": """
## Plainspeak
This feature acts as an intelligent traffic cop for your AI requests.

When an AI provider like OpenAI goes down, normally your users just get an error screen. With this feature, the system instantly notices the outage and automatically detours the traffic to a working backup provider (like Anthropic) in the blink of an eye. The user never even notices there was a problem.
""",
    
    "advanced-threat-defense-enterprise-resilience/antifragile-exponential-retries.md": """
## Plainspeak
This feature teaches the system how to be patient and polite when the internet is struggling.

Sometimes a server gets overwhelmed and drops a connection. Instead of immediately hammering the server with a million retry requests (which just makes the crash worse), this feature forces the proxy to wait a little bit, then try again. If it fails again, it waits a little bit *longer*. This elegant, increasing delay gives the broken server time to recover.
""",
    
    "advanced-threat-defense-enterprise-resilience/composite-agent-loop-circuit-breaker.md": """
## Plainspeak
This feature acts as an emergency stop button for autonomous AI agents that get stuck in infinite loops.

Sometimes, an AI agent is given a complex task and it gets confused, endlessly repeating the same useless actions (like searching for the same file over and over) without making progress. If left alone, this runs up a massive bill. This circuit breaker automatically detects when an AI is stuck in a repeating cycle and violently cuts the power, saving money and preventing server exhaustion.
""",
    
    "advanced-threat-defense-enterprise-resilience/traffic-engineering-resiliency.md": """
## Plainspeak
This feature acts as a smart speed limit for incoming requests to prevent your infrastructure from being overwhelmed.

If a massive spike of thousands of users suddenly tries to use the AI all at the same time, it could crash the entire system. This feature uses a specialized "token bucket" system to enforce a strict speed limit (like allowing a maximum of 6000 requests per minute). Anyone who exceeds this limit is gently told to slow down, ensuring the system stays online for everyone else.
""",
    
    "advanced-threat-defense-enterprise-resilience/graceful-shutdown-pod-drain.md": """
## Plainspeak
This feature ensures no one gets cut off mid-sentence when the proxy server needs to restart or update.

When IT engineers update the server, normally it instantly kills all active connections, resulting in broken half-written AI responses for users. With this feature, when the server is told to shut down, it stops accepting *new* users, but patiently waits for all *current* users to finish their active conversations before finally turning itself off.
""",
    
    "advanced-threat-defense-enterprise-resilience/request-id-correlation-sanitization.md": """
## Plainspeak
This feature acts as a tracking number system for your data.

When a user sends a message, it travels through a maze of different servers and programs. If something goes wrong, it's impossible to figure out where the error happened unless you can trace the message's exact path. This feature attaches a unique tracking ID to every single request. No matter where the data goes, IT engineers can use that tracking ID to find out exactly what happened to it.
""",
    
    "advanced-threat-defense-enterprise-resilience/multi-provider-upstream-key-registry.md": """
## Plainspeak
This feature works like a smart keychain that automatically grabs the right key for the right door.

If you use multiple AI providers (OpenAI, Anthropic, DeepSeek), developers usually have to write messy code to juggle all the different API passwords. With this feature, developers just send their request to the proxy, and the proxy automatically looks at the destination, pulls the correct password out of its secure keychain, and unlocks the door. Developers never have to worry about managing the keys.
""",
    
    "enterprise-auditing-compliance/worm-compliant-audit-logging-with-hash-chaining.md": """
## Plainspeak
This feature creates an unhackable, permanent diary of every security decision the proxy makes.

To pass strict security audits (like SOC 2 or HIPAA), companies need absolute proof of what happened and when. This feature records every action and mathematically locks it to the action that happened right before it (like links in a chain). If a hacker tries to go back in time to delete or change a log entry, the entire mathematical chain breaks, instantly revealing the tampering to auditors.
""",

    "enterprise-auditing-compliance/cryptographic-proof-of-non-egress-stream-attestation.md": """
## Plainspeak
This feature creates a mathematically guaranteed receipt proving that sensitive data was successfully redacted.

When an AI streams a long response, how do you prove to an auditor that no Social Security Numbers accidentally leaked out? This feature calculates a unique digital fingerprint of the data as it flows out. At the very end of the stream, it attaches this fingerprint like a wax seal. If anyone questions the security later, this seal serves as absolute mathematical proof that the data was sanitized.
""",

    "enterprise-auditing-compliance/universal-decision-trace-exporter.md": """
## Plainspeak
This feature translates the proxy's complex security decisions into a standard format that corporate monitoring tools can easily understand.

Instead of hiding its security actions in messy text files, this feature packages every decision (like why it blocked a specific word) into highly structured, government-standard data packets. It then broadcasts these packets so that your company's existing dashboards and monitoring screens can display the security data beautifully and clearly.
""",
    
    "enterprise-auditing-compliance/zero-overhead-opentelemetry-otel-tracing.md": """
## Plainspeak
This feature acts like an ultra-lightweight GPS tracker attached to every request, without slowing down the vehicle.

To monitor the health of the system, we need to track exactly how many milliseconds a request spends in each part of the proxy. However, the act of tracking can sometimes accidentally slow down the system! This feature solves that by assigning the heavy lifting of tracking to a completely separate background worker, ensuring the main traffic flows at maximum speed without any tracking delays.
""",
    
    "enterprise-auditing-compliance/grc-webhook-sidecar-file-transport.md": """
## Plainspeak
This feature acts as an automated courier that delivers compliance reports directly to the platforms that manage your company's security audits.

When the proxy makes security decisions, those logs are useless if they just sit on a server. This feature automatically bundles up the audit logs and instantly transmits them (via webhooks or sidecar files) straight into external audit software (like Vanta or Drata). This means your company's security score updates in real-time, completely automatically.
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
