# Benchmark runs

Store every evaluation attempt under a unique immutable run identifier:

```text
runs/<condition>/<case_id>/<run_id>/
  run_record.json
  prompt.txt
  system_prompt.txt
  raw_response.txt
  parsed_prediction.json
  tool_trace.jsonl
  artifacts/
```

Never use a single `latest` file for benchmark evidence. Failed, clarified, rejected, and partially completed runs are retained because failure behavior is part of the evaluation.
