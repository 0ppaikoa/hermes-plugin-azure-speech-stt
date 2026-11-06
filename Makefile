PYTHON ?= python3

.PHONY: test check leak

test:
	$(PYTHON) -m unittest discover -s tests

leak:
	@if git grep -nE "aok-foundry|default-msf|日新火災|サイゼリヤ|09045078183" -- . ; then \
		echo "leak check: real identifiers found"; exit 1; \
	else echo "leak check: clean"; fi

check: test leak
	$(PYTHON) -m compileall -q . -x '__pycache__'
	@echo "check: ok"
