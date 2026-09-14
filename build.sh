#!/bin/bash
set -e

echo "==> Instalando Deno em /opt/render/project/.deno"
curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/opt/render/project/.deno sh -s -- -y

echo "==> Verificando Deno"
if [ -x /opt/render/project/.deno/bin/deno ]; then
    /opt/render/project/.deno/bin/deno --version
else
    echo "ERRO: Deno nao foi instalado"
    exit 1
fi

echo "==> Instalando dependencias Python"
pip install -r requirements.txt

echo "==> Build completo"
