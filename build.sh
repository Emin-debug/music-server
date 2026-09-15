#!/bin/bash
set -e

echo "==> Instalando Deno em /opt/render/project/.deno"
curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/opt/render/project/.deno sh -s -- -y

if [ -x /opt/render/project/.deno/bin/deno ]; then
    echo "deno OK"
    /opt/render/project/.deno/bin/deno --version
else
    echo "ERRO: Deno nao instalado"
    exit 1
fi

echo "==> Instalando Node.js portatil em /opt/render/project/node"
NODE_VERSION="v20.11.1"
NODE_DIR="/opt/render/project/node"
mkdir -p "$NODE_DIR"
cd /tmp
curl -fsSL "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.xz" -o node.tar.xz
tar -xf node.tar.xz -C "$NODE_DIR" --strip-components=1
export PATH="$NODE_DIR/bin:$PATH"

if command -v node >/dev/null 2>&1; then
    echo "node OK"
    node --version
    npm --version
else
    echo "ERRO: Node nao instalado"
    exit 1
fi

echo "==> Instalando dependencias Python"
cd /opt/render/project/src
pip install -r requirements.txt

echo "==> Clonando bgutil-ytdlp-pot-provider 1.3.1"
rm -rf bgutil-ytdlp-pot-provider
git clone --depth 1 --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git

cd bgutil-ytdlp-pot-provider/server/

echo "==> Ajustando jsdom para 24.1.3"
node <<'EOF'
const fs = require('fs');
const path = 'package.json';
const pkg = JSON.parse(fs.readFileSync(path, 'utf8'));

if (pkg.dependencies && pkg.dependencies.jsdom) {
    console.log('jsdom antes:', pkg.dependencies.jsdom);
    pkg.dependencies.jsdom = '24.1.3';
}
if (pkg.devDependencies && pkg.devDependencies.jsdom) {
    pkg.devDependencies.jsdom = '24.1.3';
}
delete pkg.overrides;

fs.writeFileSync(path, JSON.stringify(pkg, null, 2));
console.log('jsdom depois:', (pkg.dependencies && pkg.dependencies.jsdom) || '(nao estava em deps)');
EOF

echo "==> Limpando lock e node_modules"
rm -rf node_modules package-lock.json

echo "==> npm install"
npm install --no-audit --no-fund

echo "==> Versoes instaladas"
npm ls jsdom || true
npm ls parse5 || true

echo "==> Compilando TypeScript"
npx tsc

cd /opt/render/project/src

if [ -f "bgutil-ytdlp-pot-provider/server/build/main.js" ]; then
    echo "==> bgutil compilado com sucesso"
else
    echo "ERRO: bgutil nao compilou"
    ls -la bgutil-ytdlp-pot-provider/server/
    exit 1
fi

echo "==> Build completo"
