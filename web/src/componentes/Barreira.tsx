import { Component, type ErrorInfo, type ReactNode } from "react";

/** O que aparece quando o React desiste.
 *
 * Sem isto, um erro em qualquer componente apaga a árvore inteira e
 * sobra a tela em branco — o pior sinal que uma interface pode dar,
 * porque é indistinguível de servidor fora do ar, de build errado e de
 * página que ainda não carregou. O erro real fica no console, onde só
 * quem sabe abrir o F12 encontra.
 *
 * Um erro de front costuma ter uma causa banal: o `dist` é de uma versão
 * e a API é de outra. Dizer isso na tela vale mais que qualquer stack
 * trace escondido.
 */
export class Barreira extends Component<{ children: ReactNode }, { erro: Error | null }> {
  state: { erro: Error | null } = { erro: null };

  static getDerivedStateFromError(erro: Error) {
    return { erro };
  }

  componentDidCatch(erro: Error, info: ErrorInfo) {
    console.error("o painel quebrou:", erro, info.componentStack);
  }

  render() {
    if (!this.state.erro) return this.props.children;

    return (
      <div className="quebrou">
        <h1>o painel quebrou</h1>
        <p className="mensagem">{this.state.erro.message}</p>
        <p>
          A causa mais comum é o painel estar numa versão e a API noutra. Reconstrua e recarregue
          sem cache:
        </p>
        <pre>
          cd web{"\n"}npm run build{"\n"}cd ..
        </pre>
        <p className="dica">Depois, Ctrl+F5 nesta página.</p>
      </div>
    );
  }
}
