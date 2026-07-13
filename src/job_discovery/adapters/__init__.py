from job_discovery.adapters.ashby import AshbyAdapter
from job_discovery.adapters.greenhouse import GreenhouseAdapter
from job_discovery.adapters.lever import LeverAdapter
from job_discovery.schemas import SourcePlatform

ADAPTERS = {
    SourcePlatform.GREENHOUSE: GreenhouseAdapter(),
    SourcePlatform.LEVER: LeverAdapter(),
    SourcePlatform.ASHBY: AshbyAdapter(),
}

__all__ = ["ADAPTERS", "AshbyAdapter", "GreenhouseAdapter", "LeverAdapter"]

