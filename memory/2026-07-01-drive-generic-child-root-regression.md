# Drive generic child folder regression

## Symptom

Ao sincronizar a pasta `VÍDEOS RELIGIOSOS`, o sistema retornou `0 novo(s), 53 atualizado(s)` e manteve o estoque em 150, indicando que apenas uma subpasta genérica (`Videos`) foi varrida em vez da pasta principal inteira com `Videos`, `Cortes séries` e vídeos soltos.

## Root cause

`DriveLibraryService._resolve_niche_roots()` aceitava correspondência parcial nos dois sentidos:

- `wanted in normalized`
- `normalized in wanted`

Assim, se a metadata da pasta raiz não estivesse disponível, o filho `Videos` normalizava para `videos` e casava com o nicho `videos religiosos`, virando a raiz errada da sincronização.

## Fix

Manter correspondência exata e permitir parcial apenas quando o nome da pasta contém o nicho completo (`wanted in normalized`). Pastas genéricas como `Videos`, `Audios` e `Cortes séries` não podem virar raiz do nicho.

## Evidence

`python -m unittest backend.tests.test_drive_library backend.tests.test_ready_video_seo`

Resultado: 12 testes OK.

## Regression test

`backend/tests/test_drive_library.py::DriveLibraryTests.test_resolve_niche_does_not_choose_generic_child_folder`

## Status

DONE
