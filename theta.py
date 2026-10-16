"""ThetaData client setup plus a throttled, retrying call helper.

The API key is never printed. make_client() copies THETADATA_API_KEY from .env into the
environment first, so .env always wins over an old key inherited from the shell.
"""
import os
import threading
import time

import grpc
from dotenv import dotenv_values
from thetadata import ThetaClient
from thetadata.errors import NoDataFoundError

import config

# gRPC codes worth retrying (queue full / server busy / network hiccup).
RETRYABLE_CODES = {
    grpc.StatusCode.RESOURCE_EXHAUSTED,
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
}

_last_request_time = 0.0
_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_REQUESTS)   # requests in flight
_spacing = threading.Lock()                                            # spacing between request starts


def apply_ca_bundle():
    if config.CA_BUNDLE:
        # httpx (login, FRED) reads SSL_CERT_FILE; gRPC (data) reads GRPC_DEFAULT_SSL_ROOTS_FILE_PATH.
        os.environ["SSL_CERT_FILE"] = str(config.CA_BUNDLE)
        os.environ["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] = str(config.CA_BUNDLE)


def use_dotenv_key():
    """Set THETADATA_API_KEY from .env, replacing any value already in the environment."""
    key = dotenv_values(config.DOTENV_PATH).get("THETADATA_API_KEY")
    if key:
        os.environ["THETADATA_API_KEY"] = key


def make_client():
    apply_ca_bundle()
    use_dotenv_key()
    return ThetaClient(dotenv_path=config.DOTENV_PATH, dataframe_type="pandas")


def call(fn, **kwargs):
    """Run a request with at most MAX_CONCURRENT_REQUESTS in flight (safe from several threads),
    starts spaced by MIN_SECONDS_BETWEEN_REQUESTS, with retries."""
    global _last_request_time
    delay = config.RETRY_BACKOFF_SECONDS
    for attempt in range(1, config.MAX_RETRIES + 1):
        with _slots:
            with _spacing:
                wait = config.MIN_SECONDS_BETWEEN_REQUESTS - (time.monotonic() - _last_request_time)
                if wait > 0:
                    time.sleep(wait)
                _last_request_time = time.monotonic()
            try:
                return fn(**kwargs)
            except NoDataFoundError:
                raise  # not transient: the query matched nothing
            except grpc.RpcError as e:
                if e.code() not in RETRYABLE_CODES or attempt == config.MAX_RETRIES:
                    raise
                print(f"  {fn.__name__}: {e.code().name}, retry {attempt} in {delay:.0f}s")
        time.sleep(delay)          # back off outside the slot so the other request can run
        delay *= 2
