PYTHON ?= python3
DEPS ?= /home/hj/userfeedback/uid4-0042/.deps
PYTHONPATH := $(DEPS):$(CURDIR)/src
export PYTHONPATH

.PHONY: all sources experiment partition plots report verify clean

all: sources experiment partition plots report verify

sources:
	$(PYTHON) src/generate_sources.py

experiment:
	$(PYTHON) src/run_experiment.py

partition:
	$(PYTHON) src/partition_test.py

plots:
	$(PYTHON) src/make_detail_crops.py

report:
	$(PYTHON) src/make_report.py

verify:
	$(PYTHON) src/verify_results.py

clean:
	rm -rf artifacts/work artifacts/encoded artifacts/results
