.PHONY: install run run-force fetch transform clean lint

install:
	python3 -m pip install -r requirements.txt

run:
	python3 -m pipeline.run_all

run-force:
	python3 -m pipeline.run_all --force

fetch:
	python3 -m pipeline.run_all --step fetch

transform:
	python3 -m pipeline.run_all --step transform

clean:
	rm -rf data/raw/ data/processed/*.parquet data/processed/*.csv

lint:
	python3 -m py_compile pipeline/config.py pipeline/fetch_apr.py pipeline/transform.py pipeline/run_all.py && echo "OK"
