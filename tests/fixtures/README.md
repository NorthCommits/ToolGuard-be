# Fixture Corpus

Each fixture is a JSON manifest that ingestion accepts as is, plus two keys that ingestion ignores:

- notes: what the fixture represents and why.
- expected: what the pipeline must produce.
  - verdict: pass, review, or fail, at the default fail_on threshold (high).
  - findings: a list of {"tool": tool name, "rule_id": rule ID} pairs that must be reported. Other findings may also appear unless the fixture is benign.

Hidden and look-alike characters are written as \u escapes so they are visible in review.

## Safety

The malicious fixtures are intentionally hostile test data. They are only ever parsed as text and must never be executed, sent to an agent with tools, or used as instructions. They use only reserved example domains (example.com, example.net) and obviously fake values; file paths such as ~/.ssh/id_rsa appear as text only. They contain no real secrets, keys, or personal data. Keep it that way when adding fixtures.

## Folders

- benign/: known-good manifests. Expected verdict is always pass with no expected findings.
- malicious/: manifests with expected findings. This includes risky but not malicious manifests, such as weak_schemas.json, whose expected verdict is review.

## Metrics (reported by the pytest regression suite from M6)

- Detection rate: the share of malicious/ fixtures whose verdict is review or fail and whose expected findings are all reported.
- False positive rate: the share of benign/ fixtures whose verdict is not pass. It is measured on benign/ only, so risky fixtures in malicious/ never count as false positives.

Expected findings are written against the rule catalog in sdd.md section 6.2. They are checked from M2, when the rules exist.
