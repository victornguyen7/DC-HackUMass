import os

# Tests never call a real model, even when engine/.env holds a Gemini or Groq key.
os.environ["LLM_PROVIDER"] = "mock"

# Tests never write to Tiger (the forecast ledger is append-only). Empty values beat engine/.env,
# whose loader only sets missing keys. The DB round-trip test connects via DCSIM_TSDB_TEST_URL itself.
os.environ["DCSIM_TSDB_URL"] = ""
os.environ["TIGER_SERVICE_ID"] = ""
