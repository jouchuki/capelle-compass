"""capelle-cube — general OLAP-cube probe playbook for arbitrary tabular data.

Schema inference + 11 typed analytical probes (trajectory, share_drift,
interactions, unit_price, quality_ranking, coverage, concentration,
anomaly_scan, sub_annual, relative_pricing, age_alignment). Dataset-agnostic
— point it at any CSV with (time × dimensions × metrics) shape.
"""
