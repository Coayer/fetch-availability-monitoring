#!/usr/bin/env python3
import yaml
import logging
import sys
import aiohttp
import asyncio
import time
from collections import defaultdict
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

REFRESH_PERIOD = 15
DOWN_REQUEST_TIMEOUT = 0.5  # For down detection


def load_config(config_file_path: str) -> defaultdict[str, list]:
    """Loads domain monitoring configuration from YAML file."""
    try:
        with open(config_file_path, "r") as file:
            return parse_config(file.read())
    except Exception as e:  # Any failure in reading/parsing requires exit
        logging.error(f"Failed to load config: {e}")
        sys.exit(1)


def parse_config(config):
    """Parse YAML string into data structure for monitoring.

    Parameters:
        config: YAML string

    Returns:
        A dictionary mapping domain names to a list of monitored endpoints stored in dictionaries.
        For example:

        {
            "example.com": [
                {
                "name": "Index of example.com",
                "request_parameters": {
                    "url": "https://example.com/",
                    "method": "GET",
                    "headers": {
                        "user-agent": "my-user-agent"
                    },
                    "data": None
                }
                }
            ]

        }
    """
    file_content = yaml.safe_load(config)

    monitored_domains = defaultdict(list)

    for entry in file_content:
        domain = urlparse(entry["url"]).netloc
        monitored_domains[domain].append(
            {
                "name": entry["name"],
                "request_parameters": {
                    "url": entry["url"],
                    "method": entry.get("method", "GET"),  # Defaults to "GET"
                    "headers": entry.get("headers", None),
                    "data": entry.get("body", None),
                },
            }
        )

    return monitored_domains


async def check_endpoint_status(session: aiohttp.ClientSession, endpoint: dict) -> bool:
    """Get current status of endpoint as a bool.

    Parameters:
        session: aiohttp session to make requests with
        endpoint: Dictionary which will be used as parameters to an HTTP request

    Returns:
        Up (True) or Down (False) status of endpoint.
    """
    try:
        async with session.request(
                **endpoint, timeout=DOWN_REQUEST_TIMEOUT
        ) as response:
            response = response.status
            return 200 <= response < 300
    except TimeoutError:  # Expected behavior so not interested in logging
        return False
    except Exception as e:  # Other errors might need to be investigated
        logging.error(f"Encountered problem fetching {endpoint["url"]}: {e}")
        return False


async def get_domain_availability(
        session: aiohttp.ClientSession, endpoints: list[dict]
) -> float:
    """Get fraction of endpoints that are currently available.

    Parameters:
        session: aiohttp session to make requests with
        endpoints: List of endpoints to check

    Returns:
        Fraction of given endpoints which are available.
    """
    tasks = [
        check_endpoint_status(session, endpoint["request_parameters"])
        for endpoint in endpoints
    ]
    endpoints_status = await asyncio.gather(*tasks)
    return sum(endpoints_status) / len(endpoints_status)


async def update_domains_availability(
        session: aiohttp.ClientSession,
        domain_availability_averages: dict,
        monitoring_cycle: int,
        monitored_domains: dict[str, list],
):
    """Update average availability of domains.
    
    Parameters:
        session: aiohttp session to make requests with
        domain_availability_averages: Map of domain names to previous availability average
        monitoring_cycle: Current iteration of monitoring cycle
        monitored_domains: Map of domains to endpoints
    """
    tasks = [
        get_domain_availability(session, endpoints)
        for endpoints in monitored_domains.values()
    ]
    results = await asyncio.gather(*tasks)
    for domain, availability_pct in zip(monitored_domains.keys(), results):
        domain_availability_averages[domain] = (
                                                       domain_availability_averages[domain] * (monitoring_cycle - 1)
                                                       + availability_pct
                                               ) / monitoring_cycle


async def main():
    """Main program loop."""
    if len(sys.argv) != 2:
        logging.error(
            "Must provide a single argument containing the path to a YAML config file"
        )
        sys.exit(1)

    config_file_path = sys.argv[1]
    logging.info(f"Using config file: {config_file_path}")

    monitored_domains = load_config(config_file_path)
    logging.info(f"Monitoring domains: {list(monitored_domains)}")

    domain_availability_averages = {domain: 0 for domain in monitored_domains}
    monitoring_cycle = 0
    async with aiohttp.ClientSession() as session:  # Shared so library can reuse connections
        while True:
            start_time = time.monotonic()  # Times how long the domain checking takes

            monitoring_cycle += 1
            await update_domains_availability(
                session,
                domain_availability_averages,
                monitoring_cycle,
                monitored_domains,
            )

            logging.info(
                "\n".join(
                    f"{domain} has {round(availability * 100)}% availability percentage"
                    for domain, availability in domain_availability_averages.items()
                )
            )

            # Ensures iteration period is followed regardless of time taken making requests (could be between 0 and timeout)
            elapsed_time = time.monotonic() - start_time
            await asyncio.sleep(max(0.0, REFRESH_PERIOD - elapsed_time))    # In case elapsed > refresh period


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Exiting...")
        sys.exit(0)
