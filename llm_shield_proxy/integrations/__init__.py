"""Optional adapters that run this gateway inside somebody else's host.

Every module in this subpackage imports a *host* framework (LiteLLM today) and
exists only for the environment where that host is already installed. Importing
an adapter without its host raises ``ImportError``, which is intended: it keeps
``pip install llm-shield-proxy`` free of the host's dependency tree.

Nothing here is imported by ``llm_shield_proxy/__init__.py``. The contract is
one-way -- a host may import this subpackage; this subpackage must never import
a host at package-import time, and the base install must not require one.
"""
