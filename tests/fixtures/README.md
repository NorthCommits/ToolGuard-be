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
- heldout/: hostile manifests written after the rules, from publicly documented MCP attack patterns (tool poisoning, shadowing, schema poisoning, rug pull wording, obfuscation), in our own wording. They measure overfitting: results are printed after every pytest run as fully detected and flagged (review or fail) counts, separately from the main rates, and are not asserted. Rules must not be tuned to them in the same change that adds them. A batch that has been used for tuning moves into malicious/ (its notes say so) and is replaced by a fresh batch; the fresh batch's first run is the honest held-out number. The same author writes the rules and the fixtures, so held-out results are an estimate, not a guarantee.

## Metrics (printed after every pytest run, from M2)

- Detection rate: the share of malicious/ fixtures where every expected finding is reported and the verdict equals the expected verdict. A malicious fixture may expect a verdict of pass only when all its expected rules have a default severity of low or info; a test enforces this.
- False positive rate: the share of benign/ fixtures whose verdict is not pass. It is measured on benign/ only, so risky fixtures in malicious/ never count as false positives.
- Benign fixtures with any finding: shown for information only, so low severity noise on benign data stays visible. It is not a pass or fail target.

The tests assert only the PRD targets: detection rate at least 90 percent, false positive rate under 10 percent.

Expected findings are written against the rule catalog in sdd.md section 6.2 and are checked against real rule output.
