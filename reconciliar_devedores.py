"""
CLI da reconciliacao Situacao x Turma de Controle (ver
RASCUNHO_reconciliacao_devedor.md).

So faz ANALISE (leitura) e exporta as ACOES PENDENTES (mudar status /
matricular em turma) pra um CSV de REVISAO - nunca grava nada, em nenhuma
hipotese, nem o comentario de analise nem nenhuma acao.

Revisto em 2026-09-05 (pedido explicito do usuario apos o "flood" de
comentarios automaticos gravado em 04/09/2026): este script REMOVEU de
proposito qualquer forma de aplicar um lote de aprovacoes de volta - existia
um modo "--aplicar-relatorio" que lia uma coluna 'aprovado' preenchida a
mao num CSV e gravava tudo numa rodada so, sem nenhuma confirmacao ao vivo
no momento da gravacao. Isso nao existe mais. Toda gravacao (comentario de
analise, mudanca de status, matricula em turma) exige confirmacao humana
individual no momento de gravar - use a tela "Reconciliação de Devedores"
do sistema-mascara (app.py), que confirma um aluno por vez.

Fluxo deste script (so analise + exportacao pra revisao manual):

       python reconciliar_devedores.py --relatorio pendentes.csv

Como rodar (sempre precisa disso primeiro):
  set FUCTURA_LOGIN=seu_login
  set FUCTURA_SENHA=sua_senha
"""
import argparse
import os
import sys

import reconciliacao_devedor as rec
from fuctura_client import FucturaClient, FucturaAuthError


def login_fuctura():
    login = os.environ.get("FUCTURA_LOGIN")
    senha = os.environ.get("FUCTURA_SENHA")
    if not login or not senha:
        print("Defina FUCTURA_LOGIN e FUCTURA_SENHA como variável de ambiente antes de rodar.")
        sys.exit(1)
    print(f"Logando como {login}...")
    client = FucturaClient(login, senha)
    try:
        client.entrar()
    except FucturaAuthError as e:
        print(f"Falha no login: {e}")
        sys.exit(1)
    print(f"Login OK - {client.nome_usuario}")
    return client


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limite", type=int, default=None, help="número máximo de devedores a analisar (default: todos)")
    parser.add_argument("--dias-minimos", type=int, default=rec.PRAZO_PADRAO_DIAS, help=f"prazo mínimo de dívida em aberto, em dias (default: {rec.PRAZO_PADRAO_DIAS})")
    parser.add_argument("--relatorio", metavar="ARQUIVO.csv", help="exporta as ações pendentes pra este CSV, só pra revisão (não existe mais um jeito de aplicar esse CSV em lote)")
    args = parser.parse_args()

    client = login_fuctura()
    print("Modo: SÓ ANÁLISE (nada é gravado por este script)\n")

    def progresso(analise):
        acao = f" | ação pendente: {analise['acao_sugerida']['tipo']}" if analise["acao_sugerida"] else ""
        print(f"  [analisado] {analise['nome']:45} caso={analise['caso']}{acao}")

    resultado = rec.rodar_reconciliacao(client, limite=args.limite, dias_minimos=args.dias_minimos, progresso=progresso)

    print(f"\n{'=' * 70}")
    print(f"Total analisados: {resultado['total_analisados']}")
    print("Resumo por caso:")
    for caso, qtd in resultado["resumo_por_caso"].items():
        print(f"  {caso}: {qtd}")

    if not resultado["acoes_pendentes"]:
        print("\nNenhuma ação pendente nesta rodada.")
        return

    print(f"\nAções pendentes de confirmação manual: {len(resultado['acoes_pendentes'])}")
    if args.relatorio:
        rec.exportar_relatorio_csv(resultado, args.relatorio)
        print(f"Exportado pra {args.relatorio} - só pra revisão.")
    else:
        print("(passe --relatorio ARQUIVO.csv pra exportar essas ações pra revisão)")
    print("\nNada é gravado por este script, nem em lote nem individualmente - use a tela")
    print("'Reconciliação de Devedores' do sistema-máscara pra confirmar e gravar, um aluno por vez.")


if __name__ == "__main__":
    main()
