PyGRO is a Python library for integrating geodesics in arbitrary spacetimes where users define metrics at runtime as SymPy expressions, but the geodesic integration step is painfully slow for typical research workloads like orbit fitting and ray-tracing.

Make the integration step significantly faster.

You need to:
Preserve APIs and behavior, and keep the current tolerances.
Edit the files in pygro/, and no others
Tests will call the existing APIs in Metric, GeodesicEngine, and integrate()
