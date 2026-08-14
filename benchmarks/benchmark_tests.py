import asyncio
import time
import re
import math
import random
import spacy
from presidio_analyzer import AnalyzerEngine
import tiktoken

def generate_synthetic_prompts(num_prompts=1000):
    prompts = []
    base_texts = [
        "Hello, my name is {name} and my email is {email}.",
        "Please send the invoice to {email}.",
        "My SSN is {ssn}.",
        "Hi, I need help with my account. My name is {name}.",
        "No PII in this text, just a regular question.",
        "Contact me at {email} or call me. Also my SSN is {ssn}.",
        "Can you summarize this article for me?",
        "Another string without any personal information.",
        "Name: {name}, Email: {email}, SSN: {ssn}",
        "My email is {email}. It's a very nice email."
    ]
    
    first_names = ["John", "Jane", "Alice", "Bob", "Charlie", "Diana"]
    last_names = ["Doe", "Smith", "Johnson", "Brown", "Williams"]
    domains = ["example.com", "test.org", "mail.net", "company.com"]

    for _ in range(num_prompts):
        text = random.choice(base_texts)
        name = f"{random.choice(first_names)} {random.choice(last_names)}"
        email = f"{name.lower().replace(' ', '.')}@{random.choice(domains)}"
        ssn = f"{random.randint(100, 999):03d}-{random.randint(10, 99):02d}-{random.randint(1000, 9999):04d}"
        
        prompts.append(text.format(name=name, email=email, ssn=ssn))
        
    return prompts

EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
SSN_REGEX = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')
NAME_REGEX = re.compile(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b')

def method_a_pure_regex(text):
    text = EMAIL_REGEX.sub("[EMAIL]", text)
    text = SSN_REGEX.sub("[SSN]", text)
    text = NAME_REGEX.sub("[NAME]", text)
    return text

def calculate_entropy(data: str) -> float:
    if not data:
        return 0.0
    entropy = 0.0
    length = len(data)
    for x in set(data):
        p_x = data.count(x) / length
        entropy -= p_x * math.log2(p_x)
    return entropy

def method_b_regex_entropy(text):
    def replacer(match):
        token = match.group(0)
        ent = calculate_entropy(token)
        if ent > 1.0:
            return "[REDACTED]"
        return token

    text = EMAIL_REGEX.sub(replacer, text)
    text = SSN_REGEX.sub(replacer, text)
    text = NAME_REGEX.sub(replacer, text)
    return text

nlp = spacy.load("en_core_web_sm")

def method_c_heavy_ner(text):
    doc = nlp(text)
    redacted = text
    # Extract entities and redact them
    # Iterate in reverse to maintain string indices during replacement
    for ent in reversed(doc.ents):
        if ent.label_ in ["PERSON", "ORG", "GPE", "DATE"]:
            redacted = redacted[:ent.start_char] + f"[{ent.label_}]" + redacted[ent.end_char:]
    return redacted

analyzer = AnalyzerEngine()

def method_d_presidio(text):
    results = analyzer.analyze(text=text, entities=["PERSON", "EMAIL_ADDRESS", "US_SSN"], language='en')
    redacted = text
    results.sort(key=lambda x: x.start, reverse=True)
    for res in results:
        redacted = redacted[:res.start] + f"[{res.entity_type}]" + redacted[res.end:]
    return redacted

def calculate_percentiles(latencies):
    if not latencies:
        return 0, 0, 0
    latencies.sort()
    
    def get_percentile(p):
        idx = int(len(latencies) * p)
        return latencies[min(idx, len(latencies) - 1)]

    return get_percentile(0.50), get_percentile(0.90), get_percentile(0.99)

async def run_benchmarks():
    print("Generating 1,000 synthetic prompts...")
    prompts = generate_synthetic_prompts(1000)
    
    # Calculate independent variables O(N) for IEEE rigor
    total_chars = sum(len(p) for p in prompts)
    mu_len = total_chars / len(prompts)
    
    # Rough entity density calculation based on bracket injection in Method A
    total_entities = 0
    for p in prompts:
        p_mod = method_a_pure_regex(p)
        total_entities += p_mod.count("[EMAIL]") + p_mod.count("[SSN]") + p_mod.count("[NAME]")
    rho = total_entities / len(prompts)
    
    print(f"\nEmpirical Input Constraints (Independent Variables O(N)):")
    print(f"Mean Prompt Length (mu_len): {mu_len:.1f} characters")
    print(f"Average Entity Density (rho): {rho:.1f} entities/prompt\n")
    
    results = {}

    print("Running Method A: Pure Regex matching...")
    latencies_a = []
    for prompt in prompts:
        start = time.perf_counter()
        method_a_pure_regex(prompt)
        end = time.perf_counter()
        latencies_a.append((end - start) * 1000)
    results['Method A (Pure Regex)'] = latencies_a

    print("Running Method B: Regex + Entropy...")
    latencies_b = []
    for prompt in prompts:
        start = time.perf_counter()
        method_b_regex_entropy(prompt)
        end = time.perf_counter()
        latencies_b.append((end - start) * 1000)
    results['Method B (Regex + Entropy)'] = latencies_b

    print("Running Method C: Spacy NER...")
    latencies_c = []
    for prompt in prompts:
        start = time.perf_counter()
        method_c_heavy_ner(prompt)
        end = time.perf_counter()
        latencies_c.append((end - start) * 1000)
    results['Method C (Spacy NER)'] = latencies_c

    print("Running Method D: Presidio NER...")
    latencies_d = []
    for prompt in prompts:
        start = time.perf_counter()
        method_d_presidio(prompt)
        end = time.perf_counter()
        latencies_d.append((end - start) * 1000)
    results['Method D (Presidio NER)'] = latencies_d

    print("\n" + "="*75)
    print("Benchmark Results (Latencies in milliseconds)")
    print("="*75)
    print(f"{'Method':<30} | {'p50 (ms)':<10} | {'p90 (ms)':<10} | {'p99 (ms)':<10}")
    print("-" * 75)
    
    for method, latencies in results.items():
        p50, p90, p99 = calculate_percentiles(latencies)
        print(f"{method:<30} | {p50:>10.4f} | {p90:>10.4f} | {p99:>10.4f}")
    print("="*75)

    print("\nRunning Token Savings Benchmark...")
    enc = tiktoken.get_encoding("cl100k_base")
    # Synthetic name vs bracket tag
    bracket_tag = "[PERSON_1]"
    synthetic_name = "Maya"
    bracket_tokens = len(enc.encode(bracket_tag))
    synthetic_tokens = len(enc.encode(synthetic_name))
    print(f"Tokens for '{bracket_tag}': {bracket_tokens}")
    print(f"Tokens for '{synthetic_name}': {synthetic_tokens}")
    print(f"Token savings per entity: {bracket_tokens - synthetic_tokens}")

if __name__ == "__main__":
    asyncio.run(run_benchmarks())
