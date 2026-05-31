@echo off
echo Instalando dependencias...
pip install -r requirements.txt

echo.
if not exist .env (
    copy .env.example .env
    echo Arquivo .env criado. EDITE com sua chave ANTHROPIC_API_KEY antes de continuar.
    pause
) else (
    echo .env ja existe.
)

echo.
echo Setup concluido! Para iniciar: python main.py
pause
