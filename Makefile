.PHONY: all media lhls early quality report verify

all: media lhls early quality report verify

media:
	./src/build_media.sh

lhls:
	python3 src/package_lhls.py

early:
	python3 src/early_request_test.py

quality:
	python3 src/evaluate_quality.py

report:
	python3 src/render_report.py

verify:
	python3 src/verify_results.py
