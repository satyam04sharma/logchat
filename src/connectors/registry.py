from connectors.base import ConnectorError
from connectors.docker import DockerConnector


def make_connector(kind, config, credential=None):
    if kind == 'docker': return DockerConnector(config, credential)
    if kind == 'sentry':
        from connectors.sentry import SentryConnector
        return SentryConnector(config, credential)
    # Provider APIs still require verified pagination/retention implementations.
    raise ConnectorError('connector_not_implemented')
