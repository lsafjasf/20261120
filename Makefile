.PHONY: all test native clean

all:
	python3 src/chroma_bench.py --iterations 100

test:
	python3 -m unittest discover -s tests -v

native:
	mkdir -p build
	gcc -O3 -march=native -ffast-math -Wall -Wextra \
		-o build/frame_benchmark src/frame_benchmark.c -lm

clean:
	rm -rf build src/__pycache__ tests/__pycache__
