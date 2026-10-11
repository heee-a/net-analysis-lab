"""net-analysis-lab — reproducible measurement and analysis of network latency.

Modules
-------
``collect``  cross-platform ICMP latency collector built on the OS ping binary
``quality``  non-destructive quality gates and small-sample statistics
``sim``      packet-level model of a shared-medium LAN (hub) and CSMA/CD

Every headline number produced by this package is derived from data captured at
run time and ships with an uncertainty interval.
"""

__version__ = "0.1.0"
__all__ = ["collect", "quality", "sim"]
