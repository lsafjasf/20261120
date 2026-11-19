.PHONY: setup evaluate test all clean

setup:
	./scripts/setup.sh

evaluate:
	PYTHONPATH=src python3 scripts/evaluate.py

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

all: setup evaluate test

clean:
	rm -rf artifacts/audio artifacts/figures artifacts/results.json artifacts/report.md
