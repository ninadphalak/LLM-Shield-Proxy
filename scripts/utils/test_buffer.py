from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.streaming.streaming import SSERehydrationBuffer

vault = Vault(synthetic=False)
vault.get_or_create_token("sarah@skynet.com", "EMAIL")
buffer = SSERehydrationBuffer(vault)

json_str = '{"content": "User email is [EM"}'
print("INPUT:", json_str)
result = buffer.process_delta_text(json_str)
print("OUTPUT:", result)
print("BUFFER:", buffer.content_buffer)
