"""
Banco de produtos — agora usa a camada de DB real (Postgres/SQLite).
Sobrevive a deploys e reinícios.
"""
import uuid
from datetime import datetime

try:
    from servidor import db
except ImportError:
    import db

COLECAO = "produtos"


def listar() -> list[dict]:
    # Semeia os produtos iniciais UMA ÚNICA VEZ (controlado por marcador).
    # Assim, se você apagar todos, eles NÃO voltam.
    if not db.get("_sistema", "produtos_semeados"):
        _semear_iniciais()
        db.put("_sistema", "produtos_semeados", {"feito": True})
    return db.listar(COLECAO)


def obter(pid: str) -> dict | None:
    return db.get(COLECAO, pid)


def criar(dados: dict) -> dict:
    pid = str(uuid.uuid4())[:8]
    produto = {
        "id":            pid,
        "ativo":         dados.get("ativo", True),
        "criado_em":     datetime.now().isoformat(),
        "atualizado_em": datetime.now().isoformat(),
        **dados,
        "id":            pid,  # garante que não é sobrescrito
    }
    db.put(COLECAO, pid, produto)
    return produto


def atualizar(pid: str, dados: dict) -> dict | None:
    existente = db.get(COLECAO, pid)
    if not existente:
        return None
    atualizado = {**existente, **dados, "id": pid, "atualizado_em": datetime.now().isoformat()}
    db.put(COLECAO, pid, atualizado)
    return atualizado


def deletar(pid: str) -> bool:
    return db.deletar(COLECAO, pid)


def _semear_iniciais():
    """Cria produtos padrão na primeira execução."""
    iniciais = [
        {"id":"prod001","ativo":True,"titulo":"Fone Over-Ear ANC Pro","descricao":"Cancelamento ativo de ruído, 40h bateria, Bluetooth 5.3","imagem":"https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=400&h=400&fit=crop&q=80","preco_venda":189,"preco_de":389,"avaliacao":4.9,"vendas":312,"badge":"Mais vendido","categoria":"fones","link_aliexpress":"https://www.aliexpress.com/"},
        {"id":"prod002","ativo":True,"titulo":"Powerbank 20.000mAh Ultra Slim","descricao":"Carga rápida 22W, 2 saídas USB + USB-C, display LED","imagem":"https://images.unsplash.com/photo-1606220945770-b5b6c2c55bf1?w=400&h=400&fit=crop&q=80","preco_venda":97,"preco_de":219,"avaliacao":4.8,"vendas":541,"badge":"Oferta","categoria":"powerbanks","link_aliexpress":"https://www.aliexpress.com/"},
        {"id":"prod003","ativo":True,"titulo":"Earbuds TWS Pro 5.3","descricao":"IPX5, 36h bateria total, modo game 45ms, ANC","imagem":"https://images.unsplash.com/photo-1590658268037-6bf12165a8df?w=400&h=400&fit=crop&q=80","preco_venda":127,"preco_de":259,"avaliacao":4.7,"vendas":894,"badge":"Novo","categoria":"fones","link_aliexpress":"https://www.aliexpress.com/"},
        {"id":"prod004","ativo":True,"titulo":"Carregador GaN 65W 3 Portas","descricao":"USB-C + 2x USB-A, carrega notebook, cell e tablet juntos","imagem":"https://images.unsplash.com/photo-1609091839311-d5365f9ff1c5?w=400&h=400&fit=crop&q=80","preco_venda":89,"preco_de":199,"avaliacao":4.9,"vendas":726,"badge":"Top rated","categoria":"carregadores","link_aliexpress":"https://www.aliexpress.com/"},
        {"id":"prod005","ativo":True,"titulo":"Mouse Sem Fio Silencioso 2.4G","descricao":"1600 DPI, 18 meses bateria, clique silencioso, ergonômico","imagem":"https://images.unsplash.com/photo-1527864550417-7fd91fc51a46?w=400&h=400&fit=crop&q=80","preco_venda":67,"preco_de":149,"avaliacao":4.8,"vendas":2107,"badge":"Mais pedido","categoria":"acessorios","link_aliexpress":"https://www.aliexpress.com/"},
        {"id":"prod006","ativo":True,"titulo":"Ring Light LED 26cm","descricao":"3 tons de cor, 10 níveis brilho, suporte celular incluso","imagem":"https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=400&h=400&fit=crop&q=80","preco_venda":84,"preco_de":179,"avaliacao":4.7,"vendas":673,"badge":"Em alta","categoria":"acessorios","link_aliexpress":"https://www.aliexpress.com/"},
    ]
    agora = datetime.now().isoformat()
    for p in iniciais:
        p["criado_em"] = agora
        p["atualizado_em"] = agora
        db.put(COLECAO, p["id"], p)
