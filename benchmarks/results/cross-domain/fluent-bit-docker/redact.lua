-- Redacts a PEM private key block inside the "log" field of each record. Lua patterns, not
-- regex; the key is on one physical line in this experiment (newlines became spaces).
function redact(tag, timestamp, record)
  local log = record["log"]
  if type(log) == "string" then
    local out, n = string.gsub(log, "%-%-%-%-%-BEGIN [A-Z ]*PRIVATE KEY%-%-%-%-%-[%w+/= ]+%-%-%-%-%-END [A-Z ]*PRIVATE KEY%-%-%-%-%-", "[REDACTED]")
    record["log"] = out
    record["redactions"] = n
    return 1, timestamp, record
  end
  return 0, timestamp, record
end
