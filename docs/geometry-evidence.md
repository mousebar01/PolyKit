# Geometry evidence

`geometry-evidence/swept-arc-audit` accepts a JSON point cloud for an executed
component. It fits the cloud's best plane, searches for the circular arc whose
radius is most stable, and reports bend radius, angular span, planarity, centre
distance, and radial residual. Configurable gates reject a straight rod that
only occupies roughly the same silhouette cells as a hook.

The node measures geometry that was actually produced; it does not infer a
curve from a name or from authoring parameters. The point cloud should contain
enough samples along the sweep and any expected bend radius must be supplied
explicitly when a calibrated value is available.
