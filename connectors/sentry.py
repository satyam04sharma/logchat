import re
from urllib.parse import quote
import httpx
from connectors.base import ConnectorError
from connectors.docker import normalize, timestamp


class SentryConnector:
    def __init__(self, config, credential=None, transport=None):
        self.config=config; self.credential=credential; self.transport=transport
        if not credential: raise ConnectorError('credential_missing')
        self.url='https://sentry.io/api/0/projects/'+quote(config['organization'],safe='')+'/'+quote(config['project'],safe='')+'/events/'

    async def probe(self, since, until): return None

    async def fetch(self, since, until):
        params={'start':since.isoformat(), 'end':until.isoformat()}
        count=0
        async with httpx.AsyncClient(transport=self.transport, timeout=30, follow_redirects=False, headers={'Authorization':'Bearer '+self.credential}) as client:
            for _ in range(100):
                try:
                    response=await client.get(self.url,params=params)
                    response.raise_for_status(); rows=response.json()
                    if not isinstance(rows,list): raise ValueError()
                except Exception: raise ConnectorError('fetch_failed') from None
                for row in rows:
                    tags={item.get('key'):item.get('value') for item in row.get('tags',[])}
                    if self.config.get('provider_environment') and tags.get('environment') != self.config['provider_environment']: continue
                    try: ts=timestamp(row['dateCreated'])
                    except (KeyError,ValueError): raise ConnectorError('invalid_event_timestamp') from None
                    if since<=ts<until:
                        count+=1
                        if count>10000: raise ConnectorError('batch_limit_exceeded')
                        yield normalize(row.get('message') or row.get('title') or '',ts,'sentry',self.config.get('service','sentry'),row['eventID'])
                # Only extract the provider cursor; never follow an arbitrary Link URL with credentials.
                next_link=next((part for part in response.headers.get('link','').split(',') if 'rel="next"' in part and 'results="true"' in part),None)
                if not next_link: return
                match=re.search(r'cursor="([^"]+)"',next_link)
                if not match: raise ConnectorError('pagination_incomplete')
                params['cursor']=match[1]
        raise ConnectorError('page_limit_exceeded')
