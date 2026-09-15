#!/bin/bash
set -e

echo "==> Instalando Deno em /opt/render/project/.deno"
curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/opt/render/project/.deno sh -s -- -y

if [ -x /opt/render/project/.deno/bin/deno ]; then
    /opt/render/project/.deno/bin/deno --version
else
    echo "ERRO: Deno nao foi instalado"
    exit 1
fi

echo "==> Instalando Node.js 20"
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs

node --version
npm --version

echo "==> Instalando dependencias Python"
pip install -r requirements.txt

echo "==> Clonando e compilando bgutil-ytdlp-pot-provider"
if [ -d "bgutil-ytdlp-pot-provider" ]; then
    rm -rf bgutil-ytdlp-pot-provider
fi
git clone --single-branch --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git
cd bgutil-ytdlp-pot-provider/server/
npm install
npx tsc
cd ../..

echo "==> Verificando build do bgutil"
if [ -f "bgutil-ytdlp-pot-provider/server/build/main.js" ]; then
    echo "bgutil compilado com sucesso"
else
    echo "ERRO: bgutil nao compilou"
    exit 1
fi

echo "==> Build completo"
