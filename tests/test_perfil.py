from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from licita_radar.config.perfil import ErroDePerfil, carregar_perfil


def _escrever(tmp_path: Path, dados: dict[str, Any]) -> Path:
    caminho = tmp_path / "perfil.yaml"
    caminho.write_text(yaml.safe_dump(dados, allow_unicode=True), encoding="utf-8")
    return caminho


class TestCargaFeliz:
    def test_carrega_e_normaliza(self, tmp_path: Path, perfil_valido: dict[str, Any]) -> None:
        perfil = carregar_perfil(_escrever(tmp_path, perfil_valido))

        assert perfil.id == "acme-software"
        assert perfil.restricoes.ufs == ["GO"]  # veio "go" minúsculo no YAML
        assert perfil.pontuacao.limiar_alerta == 0.72

    def test_palavras_chave_ficam_prontas_para_comparacao(
        self, tmp_path: Path, perfil_valido: dict[str, Any]
    ) -> None:
        perfil = carregar_perfil(_escrever(tmp_path, perfil_valido))

        assert "sustentacao de sistemas" in perfil.palavras_chave.positivas_normalizadas
        assert "cabeamento estruturado" in perfil.palavras_chave.negativas_normalizadas

    def test_expande_variavel_de_ambiente(
        self, tmp_path: Path, perfil_valido: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001234567890")
        perfil = carregar_perfil(_escrever(tmp_path, perfil_valido))

        assert perfil.canais[0].chat_id == "-1001234567890"

    def test_modalidades_padrao_quando_omitidas(
        self, tmp_path: Path, perfil_valido: dict[str, Any]
    ) -> None:
        del perfil_valido["restricoes"]["modalidades"]
        perfil = carregar_perfil(_escrever(tmp_path, perfil_valido))

        assert 6 in perfil.restricoes.modalidades  # pregão eletrônico entra por padrão


class TestErrosExplicaveis:
    def test_arquivo_ausente_diz_o_que_fazer(self, tmp_path: Path) -> None:
        with pytest.raises(ErroDePerfil, match=re.escape("perfil.exemplo.yaml")):
            carregar_perfil(tmp_path / "nao_existe.yaml")

    def test_yaml_quebrado_aponta_o_arquivo(self, tmp_path: Path) -> None:
        caminho = tmp_path / "perfil.yaml"
        caminho.write_text("id: acme\n  nome: [torto", encoding="utf-8")

        with pytest.raises(ErroDePerfil, match="não é um YAML válido"):
            carregar_perfil(caminho)

    def test_arquivo_vazio(self, tmp_path: Path) -> None:
        caminho = tmp_path / "perfil.yaml"
        caminho.write_text("", encoding="utf-8")

        with pytest.raises(ErroDePerfil, match="vazio"):
            carregar_perfil(caminho)

    def test_descricao_curta_demais_para_virar_embedding(
        self, tmp_path: Path, perfil_valido: dict[str, Any]
    ) -> None:
        # Institucional e vago: casa com qualquer coisa e com nada.
        perfil_valido["descricao"] = "Empresa de tecnologia e inovação para o setor público."

        with pytest.raises(ErroDePerfil, match="12 palavras"):
            carregar_perfil(_escrever(tmp_path, perfil_valido))

    def test_pesos_que_nao_somam_um(self, tmp_path: Path, perfil_valido: dict[str, Any]) -> None:
        perfil_valido["pontuacao"] = {"peso_lexical": 0.5, "peso_semantico": 0.9}

        with pytest.raises(ErroDePerfil, match=re.escape("deve dar 1.0")):
            carregar_perfil(_escrever(tmp_path, perfil_valido))

    def test_uf_invalida(self, tmp_path: Path, perfil_valido: dict[str, Any]) -> None:
        perfil_valido["restricoes"]["ufs"] = ["Goiás"]

        with pytest.raises(ErroDePerfil, match="duas letras"):
            carregar_perfil(_escrever(tmp_path, perfil_valido))

    def test_esfera_inexistente(self, tmp_path: Path, perfil_valido: dict[str, Any]) -> None:
        perfil_valido["restricoes"]["esferas"] = ["X"]

        with pytest.raises(ErroDePerfil, match="Federal"):
            carregar_perfil(_escrever(tmp_path, perfil_valido))

    def test_modalidade_inexistente(self, tmp_path: Path, perfil_valido: dict[str, Any]) -> None:
        perfil_valido["restricoes"]["modalidades"] = [6, 99]

        with pytest.raises(ErroDePerfil, match="1 a 13"):
            carregar_perfil(_escrever(tmp_path, perfil_valido))

    def test_mensagem_lista_o_campo_problematico(
        self, tmp_path: Path, perfil_valido: dict[str, Any]
    ) -> None:
        del perfil_valido["nome"]

        with pytest.raises(ErroDePerfil) as capturado:
            carregar_perfil(_escrever(tmp_path, perfil_valido))

        assert "nome" in str(capturado.value)
