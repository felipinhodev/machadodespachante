-- -----------------------------------------------------------
-- ESTRUTURA DO BANCO DE DADOS (SQLite) - DESPACHANTE.DB
-- -----------------------------------------------------------

-- 1. Tabela de Usuarios (Corrigido: coluna 'login' para combinar com o modelo)
CREATE TABLE IF NOT EXISTS usuario (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    login TEXT NOT NULL UNIQUE, -- Nome corrigido (era 'username')
    senha_hash TEXT NOT NULL,
    nivel_acesso TEXT NOT NULL CHECK(nivel_acesso IN ('COLABORADOR', 'MASTER', 'ADMIN'))
);

---

-- 2. Tabela de Clientes (Adicionado: 'endereco' e 'data_cadastro' para combinar com o modelo)
CREATE TABLE IF NOT EXISTS cliente (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    cpf_cnpj TEXT NOT NULL UNIQUE, -- CPF ou CNPJ (somente números)
    telefone TEXT,
    email TEXT,
    endereco TEXT,              -- Adicionado
    data_cadastro DATE          -- Adicionado
);

---

-- 3. Tabela de Servicos (Removido 'saldo_pendente' e adicionado 'detalhes')
CREATE TABLE IF NOT EXISTS servico (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id INTEGER NOT NULL,
    tipo_servico TEXT NOT NULL,
    detalhes TEXT,              -- Adicionado
    placa_veiculo TEXT,         
    data_servico DATE NOT NULL,
    data_vencimento DATE,
    
    valor_total REAL NOT NULL DEFAULT 0.00,
    valor_recebido REAL NOT NULL DEFAULT 0.00,
    -- NOTA: 'saldo_pendente' FOI REMOVIDO, POIS É CALCULADO.
    
    status_processo TEXT NOT NULL CHECK(status_processo IN ('Pendente', 'Em Andamento', 'Aguardando Retirada', 'Concluído', 'Cancelado')),
    status_pagamento TEXT NOT NULL CHECK(status_pagamento IN ('A Cobrar', 'Parcial', 'Pago', 'Não Cobrado')),

    FOREIGN KEY (cliente_id) REFERENCES cliente (id)
);

---

-- 4. NOVO: Tabela ItemServico (Essencial para o cálculo de valor_total)
CREATE TABLE IF NOT EXISTS item_servico (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    servico_id INTEGER NOT NULL,
    descricao TEXT NOT NULL,
    valor REAL NOT NULL DEFAULT 0.0,
    
    FOREIGN KEY (servico_id) REFERENCES servico (id)
);

---

-- 5. Tabela MovimentacaoCaixa (Corrigido: 'referencia_tipo' adicionado)
CREATE TABLE IF NOT EXISTS movimentacao_caixa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL CHECK(tipo IN ('Entrada', 'Saida', 'ENTRADA', 'SAÍDA')), -- Flexibilizado
    valor REAL NOT NULL,
    data DATE NOT NULL,
    descricao TEXT NOT NULL,
    referencia_id INTEGER, 
    referencia_tipo TEXT, -- Adicionado (era 'categoria' no schema antigo, mas o modelo usa 'referencia_tipo')
    
    FOREIGN KEY (referencia_id) REFERENCES servico (id) -- Opcional, mantido como referência para serviço
);

---

-- 6. Tabela Despesa (Referenciado no código Python, embora pouco usado)
CREATE TABLE IF NOT EXISTS despesa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data DATE NOT NULL,
    valor REAL NOT NULL,
    descricao TEXT NOT NULL,
    categoria TEXT NOT NULL, -- ⭐ CORRIGIDO/ADICIONADO: Essencial para o relatório
    paga INTEGER NOT NULL DEFAULT 0 CHECK(paga IN (0, 1))
);

---
-- -----------------------------------------------------------
-- ÍNDICES (Opcional, mas melhora a performance de busca)
-- -----------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_cliente_cpf_cnpj ON cliente (cpf_cnpj);
CREATE INDEX IF NOT EXISTS idx_servico_cliente_id ON servico (cliente_id);
CREATE INDEX IF NOT EXISTS idx_servico_placa ON servico (placa_veiculo); 
CREATE INDEX IF NOT EXISTS idx_movimentacao_caixa_data ON movimentacao_caixa (data);
CREATE INDEX IF NOT EXISTS idx_item_servico_servico_id ON item_servico (servico_id);