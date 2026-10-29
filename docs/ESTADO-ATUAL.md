# Estado atual — Meshy Image to 3D Toolkit

Revisão documental: **13/10/2026**, America/Sao_Paulo. Git inicial limpo em `main`.

## Observado/documentado

`package.json` declara Node >=18, CLIs para image-to-3d, multi-image-to-3d e print, wrappers PowerShell de crop/repair, helpers Python para split por cor e `mesh-quality`. O README pede Python >=3.10, chave Meshy e Blender opcional.

`src/mesh_quality.py` foi lido seletivamente: consome um JSON de métricas, calcula cinco checks e gera Markdown/JSON. Não carrega a malha para medir automaticamente os campos fornecidos. Os outputs/imagens sintéticos apresentados no README são demonstrações; geração real e printabilidade não foram confirmadas.

## Operação identificada

Scripts npm: `image-to-3d`, `multi-image-to-3d`, `meshy-print`, `crop-multiview`, `split-by-color`, `repair-voxel`, `mesh-quality` e `check`. `check` reúne Node syntax checks e `python -m unittest discover -s tests -v`. O README fornece exemplos; todos **não executado nesta tarefa**.

O nome público de configuração é `MESHY_API_KEY`; seu valor/arquivo de ambiente não foi lido. Nenhuma chamada API, download, Blender ou helper foi executado.

## Retomada

Última etapa comprovada: organização e conferência documental. Resultados da API, validade de GLB/STL/3MF, split, reparo e testes continuam sem validação nova.

Sugestões não aprovadas:

1. Em tarefa autorizada, obter métricas confiáveis da malha antes de interpretar readiness.
2. Preservar originais e registrar parâmetros/artefato de uma eventual geração/reparação.
3. Validar suíte e geometria com os arquivos de trabalho corretos, sem confundir fixtures com cliente real.

[Arquitetura](ARQUITETURA.md) e [índice](INDEX.md).
