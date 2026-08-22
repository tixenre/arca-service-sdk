"""Tests de arca_service_client.validadores — normalizar/validar/formatear CUIT. Puros, sin red."""

from __future__ import annotations

import pytest

from arca_service_client.validadores import cuit_valido, formatear_cuit, normalizar_cuit

_CUIT_VALIDO = "20301234563"  # verificado contra el algoritmo mod-11


class TestNormalizarCuit:
    def test_sin_guiones_pasa_tal_cual(self):
        assert normalizar_cuit(_CUIT_VALIDO) == _CUIT_VALIDO

    def test_con_guiones_normaliza_igual(self):
        assert normalizar_cuit("20-30123456-3") == _CUIT_VALIDO

    def test_con_espacios_normaliza_igual(self):
        assert normalizar_cuit(" 20 30123456 3 ") == _CUIT_VALIDO

    def test_acepta_int(self):
        assert normalizar_cuit(20301234563) == _CUIT_VALIDO

    def test_none_da_none(self):
        assert normalizar_cuit(None) is None

    def test_vacio_da_none(self):
        assert normalizar_cuit("") is None

    def test_largo_incorrecto_da_none(self):
        assert normalizar_cuit("123") is None
        assert normalizar_cuit("203012345631234") is None


class TestCuitValido:
    def test_cuit_valido_da_true(self):
        assert cuit_valido(_CUIT_VALIDO) is True

    def test_con_guiones_da_el_mismo_resultado_que_sin_guiones(self):
        assert cuit_valido("20-30123456-3") == cuit_valido(_CUIT_VALIDO) is True

    def test_digito_verificador_invalido_da_false(self):
        assert cuit_valido("20301234560") is False

    def test_malformado_da_false_no_explota(self):
        assert cuit_valido("no es un cuit") is False
        assert cuit_valido(None) is False


class TestFormatearCuit:
    def test_formatea_con_guiones(self):
        assert formatear_cuit(_CUIT_VALIDO) == "20-30123456-3"

    def test_acepta_entrada_ya_formateada_ida_y_vuelta(self):
        assert formatear_cuit("20-30123456-3") == "20-30123456-3"

    def test_acepta_int(self):
        assert formatear_cuit(20301234563) == "20-30123456-3"

    def test_malformado_levanta_value_error(self):
        with pytest.raises(ValueError):
            formatear_cuit("123")
