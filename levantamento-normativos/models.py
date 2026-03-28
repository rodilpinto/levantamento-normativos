"""
Modelos de dados para o Levantamento de Normativos.

Define as dataclasses centrais usadas em todo o pipeline:
- NormativoResult: um normativo encontrado por qualquer fonte de busca.
- SearchConfig: parametros de configuracao de uma sessao de busca.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class NormativoResult:
    """Representa um normativo ou ato encontrado durante a busca.

    O campo ``id`` e gerado automaticamente em ``__post_init__`` como o
    hash SHA-256 da concatenacao ``tipo|numero|data``.  Esse hash e usado
    para deduplicacao de resultados vindos de fontes diferentes.

    Attributes:
        id: SHA-256 hex digest de ``f"{tipo}|{numero}|{data}"``.
             Gerado automaticamente -- nao passar no construtor.
        nome: Nome completo do normativo.
              Ex: "Lei n. 13.709, de 14 de agosto de 2018 (LGPD)".
        tipo: Categoria do ato.  Valores esperados:
              "Lei", "Decreto", "Instrucao Normativa", "Portaria",
              "Acordao TCU", "Resolucao", "Framework/Padrao", "Outro".
        numero: Numero do ato (ex: "13.709").  String vazia se nao aplicavel.
        data: Data no formato DD/MM/AAAA, ou ``None`` se desconhecida.
        orgao_emissor: Orgao emissor.
              Ex: "Presidencia da Republica", "TCU", "ISACA".
        ementa: Resumo ou descricao do conteudo.
        link: URL para o documento original.
        categoria: Tema ou categoria atribuida. Default "Nao categorizado".
        situacao: Vigencia do normativo.
              "Vigente", "Revogado" ou "Nao identificado" (default).
        relevancia: Score de relevancia entre 0.0 e 1.0. Default 0.0.
        source: Identificador da fonte de busca.
              "lexml", "tcu" ou "google".
        found_by: Palavra-chave que originou o resultado.
        raw_data: Resposta bruta da API de origem. Default ``{}``.
    """

    # Campos obrigatorios (sem default) --------------------------------
    nome: str
    tipo: str
    numero: str
    data: str | None
    orgao_emissor: str
    ementa: str
    link: str
    source: str
    found_by: str

    # Campos com default ------------------------------------------------
    id: str = field(default="", init=False)
    categoria: str = "Nao categorizado"
    situacao: str = "Nao identificado"
    relevancia: float = 0.0
    raw_data: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Gera o ``id`` a partir de tipo, numero e data.

        Quando ``numero`` esta vazio (ex: resultados do Google para
        frameworks/padroes), inclui ``link`` no hash para evitar
        colisao de IDs entre resultados distintos.
        """
        if self.numero:
            raw = f"{self.tipo}|{self.numero}|{self.data}"
        else:
            raw = f"{self.tipo}|{self.link}|{self.data}"
        self.id = hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class KeywordStatus:
    """Status of a keyword search across a specific source.

    Tracks whether a keyword returned results, returned zero results
    (legitimately not found), or failed due to an API/network error.

    Attributes:
        keyword: The search term.
        source: Which searcher produced this status ("lexml", "tcu", "google").
        result_count: Number of results found (0 if not found or error).
        status: One of "ok", "empty", "error".
        error_message: Error description if status == "error", else empty.
        retried: Whether this keyword was retried after an initial error.
    """

    keyword: str
    source: str
    result_count: int = 0
    status: str = "ok"  # "ok" | "empty" | "error"
    error_message: str = ""
    retried: bool = False


@dataclass
class SearchConfig:
    """Configuracao de uma sessao de busca.

    Attributes:
        topic: Descricao em linguagem natural do tema de pesquisa.
        keywords: Lista final de palavras-chave para busca.
        sources: Fontes selecionadas.  Default ``["lexml", "tcu", "google"]``.
        max_results_per_source: Limite de resultados por fonte. Default 50.
    """

    topic: str = ""
    keywords: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=lambda: ["lexml", "tcu", "google"])
    max_results_per_source: int = 50
