# Arquitetura e regras — Meshy Image to 3D Toolkit

Fontes: `README.md`, `package.json` e `src/mesh_quality.py`.

Node.js encapsula endpoints Meshy e o README descreve normalização para Data URI, polling, timeout, captura JSON e downloads. Helpers Python em `src/` tratam crops multiview, separação GLB por cor e reparo voxel no Blender; wrappers estão em `scripts/`. Estes fluxos foram identificados pelos arquivos/manifests, sem execução.

## Avaliação de qualidade observada

`assess(metrics)` utiliza métricas fornecidas:

- `watertight` verdadeiro;
- um componente conectado;
- volume positivo;
- `thin_wall_ratio` <=0.02;
- três dimensões positivas <=300 mm (volume de demonstração).

A pontuação é a proporção de checks aprovados; `ready` exige 100%, `review` >=60% e o restante é `repair`. São critérios da triagem implementada, não garantia de fabricação. Ausência de métricas recebe defaults restritivos; o código não faz medição física ou extração automática da malha neste relatório.

O README prevê outputs ignorados, chave somente no ambiente e exemplos sintéticos. Não foi encontrado roadmap aprovado. [Estado atual](ESTADO-ATUAL.md).
