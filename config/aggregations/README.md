# Custom aggregation mappings

`aggregate_custom_gtap_model` writes reusable GTAPAgg GUI-format mappings and matching metadata here.

- `*.txt`: complete six-section GTAPAgg mapping generated from the project default mapping.
- `*.metadata.json`: model name, region/sector counts, and each original member reassignment.

Unspecified original regions and sectors retain their assignments from the default mapping. Generated RunGTAP model files are stored separately under `runtime\rungtap\<model_name>`.
