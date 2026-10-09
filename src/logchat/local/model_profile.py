"""One native model connection shared across pipeline roles.

A generation model is separate from the embedding capability. This profile is
local-only; hosted providers require a separately selected transport/policy.
"""
from __future__ import annotations


class ModelProfile:
    def __init__(self,config):
        self.config=config
        self.preserve_content=config.get('content_policy') in {'local_model_preserved','local_model_compact'}
        self.generation_model=config['generation_model'] if 'generation_model' in config else config.get('chat_model')
        if (not isinstance(self.generation_model,str) or not self.generation_model
                or len(self.generation_model)>160 or any(c.isspace() or ord(c)<32 for c in self.generation_model)):
            raise ValueError('Configure one generation model with logchat install; no default model is substituted.')
        if config.get('chunk_model') not in (None, self.generation_model):
            raise ValueError('Stage model overrides are unsupported; configure one generation model.')
        self._generators={}

    def generation(self,role='summary'):
        if role not in {'chunking','relevance','summary'}:
            raise ValueError('Unknown generation role.')
        name=self.generation_model
        if name not in self._generators:
            from pipeline.models import LocalModels
            self._generators[name]=LocalModels(base_url=self.config['base_url'],chat_model=name,
                embedding_model=self.config['embedding']['model'],preserve_content=self.preserve_content)
        return self._generators[name]

    async def embeddings(self):
        from logchat.rag.embeddings import OllamaEmbeddingProvider
        return await OllamaEmbeddingProvider.create(base_url=self.config['base_url'],
            model=self.config['embedding']['model'],dimensions=self.config['embedding']['dimensions'],
            preserve_content=self.preserve_content)

    def public(self):
        return {'provider':'ollama','endpoint':self.config['base_url'],'generation_model':self.generation_model,
                'chunking_model':self.generation_model,'embedding_model':self.config['embedding']['model'],
                'roles':['chunking','relevance','summary'],'local_only':True,
                'content_policy':self.config.get('content_policy','redacted_templates')}
