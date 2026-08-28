import os

docs_dir = "docs/features"

plainspeak_data = {
    "enterprise-auditing-compliance/dynamic-canary-watermarking-steganography.md": """
## Plainspeak
This feature acts as an invisible tracking tag to help catch leaks.

If sensitive text ever leaks out of the system, it's hard to prove who leaked it. This feature injects invisible, zero-width characters into the text response as it flows out to the user. These characters act like a secret barcode. If a malicious employee copies the text and pastes it somewhere public, the invisible barcode is copied with it. We can later scan the leaked text to instantly identify the exact user and time the text was stolen.
""",

    "enterprise-auditing-compliance/fips-140-3-kat-rfc-6902-differential-audit-logging.md": """
## Plainspeak
This feature proves to government auditors that our encryption math isn't broken.

High-security environments (like the government) don't just trust that your encryption works; they demand proof. Every time the proxy starts up, it forces itself to take a math test (encrypting a known word and checking the result). If it fails the test, it instantly shuts down, refusing to handle any real data with broken encryption.
""",

    "enterprise-auditing-compliance/nist-oscal-assessment-results-generation.md": """
## Plainspeak
This feature acts as an automatic paperwork generator for government security audits.

When a government auditor reviews your system, they usually demand massive, confusing spreadsheets detailing every single security rule. Instead of humans doing this manually, this feature automatically translates the proxy's real-time security actions into the exact, strict paperwork format (OSCAL) required by the US Government (NIST), saving hundreds of hours of manual compliance work.
""",

    "enterprise-auditing-compliance/security-response-headers-on-all-responses.md": """
## Plainspeak
This feature adds an invisible armor plating to the web browser when communicating with the proxy.

When an app connects to the internet, hackers often try tricky browser attacks (like secretly embedding your chat window inside a malicious website to steal clicks). This feature forcefully attaches strict security instructions to every single response, ordering the user's web browser to instantly block those types of attacks.
""",

    "enterprise-auditing-compliance/applied-role-name-in-audit-events.md": """
## Plainspeak
This feature acts as a strict "who authorized this?" tracker on the audit logs.

If a file is redacted, the logs normally just say "file redacted". But an auditor will ask, "Wait, why was it redacted, and whose rules were we following?" This feature automatically tags every log with the exact name of the security policy (the "role") that caused the action, providing absolute clarity on why a decision was made.
""",

    "secure-infrastructure-service-mesh/service-mesh-native-grpc-ext-proc-integration.md": """
## Plainspeak
This feature allows the proxy to operate like a high-speed internal organ of the network, rather than an external checkpoint.

Normally, sending data out to a security proxy and back wastes precious milliseconds. This feature allows the proxy to "plug in" directly to the deep plumbing of an advanced network (a Service Mesh). The data flows straight through it natively without having to leave the fast lane, making the security checks almost entirely invisible to the network speed.
""",

    "secure-infrastructure-service-mesh/centralized-enterprise-secrets-mtls.md": """
## Plainspeak
This feature guarantees that the proxy never keeps passwords lying around where a hacker could find them.

Normally, apps read their passwords from a simple file saved on the hard drive. If a hacker breaches the drive, they get the passwords. This feature forces the proxy to fetch passwords directly from an ultra-secure central vault (like HashiCorp Vault) directly into its active memory. The passwords are never saved to the hard drive, meaning there's nothing for a hacker to steal if they break in.
""",

    "secure-infrastructure-service-mesh/zero-dependency-kubernetes-mutating-webhook.md": """
## Plainspeak
This feature acts as an automatic, invisible traffic diverter for your software engineers.

If you want to force 100 different apps to route their traffic through the security proxy, you usually have to beg 100 different developers to change their code. This feature completely bypasses the developers. When they deploy their app to the cloud, this feature intercepts the deployment and invisibly edits their configuration to point to the proxy, securing the app without the developer lifting a finger.
""",

    "secure-infrastructure-service-mesh/deep-component-health-probes-and-prometheus-alert-rules.md": """
## Plainspeak
This feature acts like a highly sensitive heart monitor for the proxy.

Normally, a cloud server just checks if an app is "turned on." This feature goes much deeper. It actively tests all of the proxy's internal organs (like testing its connection to the password vault and the database). If it detects that a critical organ is failing, it immediately alerts the cloud to stop sending it traffic and pages an engineer before a major crash happens.
""",

    "secure-infrastructure-service-mesh/role-based-policy-as-code-hot-reloading.md": """
## Plainspeak
This feature allows security teams to change the rules of the proxy instantly, without rebooting the system.

Normally, if you update the security rules, you have to restart the server, which kicks everyone off their active chats. This feature constantly watches the rulebook file. The absolute second the file is updated, it smoothly slides the new rules into the system's memory. The next question asked uses the new rules, and no one's connection drops.
""",

    "secure-infrastructure-service-mesh/universal-dynamic-override-engine.md": """
## Plainspeak
This feature gives you the ultimate flexibility to change the proxy's behavior on the fly, for specific users, without rewriting the main code.

Normally, the rules of a proxy (like "always block Social Security Numbers") apply equally to everyone. This engine allows a specific user or app to send a special instruction (an "override") that says, "For this one specific question, use a different rule." It applies this temporary override seamlessly without messing up the rules for anyone else using the system at the same time.
""",

    "secure-infrastructure-service-mesh/dynamic-mcp-tool-schema-rewriting.md": """
## Plainspeak
This feature is a smart trick that allows encrypted data to seamlessly flow through external AI tools.

If we encrypt a user's ID before sending it to an AI, the AI might try to pass that encrypted gibberish to an external tool (like a database search tool), causing the tool to crash because it expects a real ID. This feature secretly sneaks a hidden tracker into the data. When the AI uses the tool, the proxy catches the request mid-air, decrypts the gibberish back into the real ID, and hands it to the tool so everything works perfectly.
""",

    "secure-infrastructure-service-mesh/uds-socket-toctou-hardening.md": """
## Plainspeak
This feature closes a tiny, split-second window of vulnerability when the proxy turns on.

When a program creates a communication pipe (a socket), there is sometimes a millisecond delay between creating the pipe and locking it with a password. A very fast hacker on the same machine could jump into the pipe during that unprotected millisecond. This feature uses advanced operating system commands to ensure the pipe is born completely locked down from the very first nanosecond.
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
