import os
# LINHA CORRIGIDA ABAIXO: Adicionando 'Response'
from flask import Flask, render_template, request, redirect, url_for, session, flash, g, Response 
from functools import wraps
from datetime import datetime, timedelta, date
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, cast, Date

# ----------------------------------------------------
# 1. CONFIGURAÇÃO BÁSICA DO FLASK E SQLALCHEMY
# ----------------------------------------------------

app = Flask(__name__)
# Chave secreta de ambiente (DEVE ser alterada em produção)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'sua_chave_secreta_padrao_muito_segura')
# Configuração do banco de dados SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///despachante.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 🚨 LINHA CRÍTICA: Ativa o log de todas as queries SQL no console
app.config['SQLALCHEMY_ECHO'] = True 

db = SQLAlchemy(app)

# ----------------------------------------------------
# 2. MODELOS DO BANCO DE DADOS (NOVO: ItemServico ADICIONADO)
# ----------------------------------------------------

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    login = db.Column(db.String(50), unique=True, nullable=False)
    senha_hash = db.Column(db.String(128), nullable=False)
    nivel_acesso = db.Column(db.String(20), default='COLABORADOR')  # ADMIN, COLABORADOR

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)

class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    cpf_cnpj = db.Column(db.String(20), unique=True, nullable=False)
    telefone = db.Column(db.String(20))
    email = db.Column(db.String(100))
    endereco = db.Column(db.String(255))
    data_cadastro = db.Column(db.Date, default=datetime.utcnow)
    
class Servico(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False)
    cliente = db.relationship('Cliente', backref=db.backref('servicos', lazy=True))
    
    tipo_servico = db.Column(db.String(150), nullable=False)
    detalhes = db.Column(db.Text)
    placa_veiculo = db.Column(db.String(10), nullable=True) # Adicionado para filtro

    data_servico = db.Column(db.Date, default=datetime.utcnow)
    data_vencimento = db.Column(db.Date) # Opcional
    
    valor_total = db.Column(db.Float, default=0.0)
    valor_recebido = db.Column(db.Float, default=0.0)
    saldo_pendente = db.Column(db.Float, default=0.0)
    
    status_processo = db.Column(db.String(50), default='Pendente') # Pendente, Em Andamento, Concluído, etc.
    status_pagamento = db.Column(db.String(50), default='Não Cobrado') # Não Cobrado, A Cobrar, Parcial, Pago

# NOVO MODELO: ItemServico para detalhamento
class ItemServico(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    valor = db.Column(db.Float, default=0.0)
    
    # Relacionamento inverso (cascade para deletar itens se o serviço for deletado)
    servico = db.relationship('Servico', backref=db.backref('itens_servico', cascade='all, delete-orphan', lazy=True))


class MovimentacaoCaixa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Date, default=datetime.utcnow)
    tipo = db.Column(db.String(10), nullable=False) # 'Entrada' ou 'Saida'
    valor = db.Column(db.Float, nullable=False)
    descricao = db.Column(db.String(255))
    referencia_id = db.Column(db.Integer) # ID do Servico ou Despesa, se aplicável
    referencia_tipo = db.Column(db.String(20)) # 'Servico' ou 'Despesa'
    
class Despesa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Date, default=datetime.utcnow)
    valor = db.Column(db.Float, nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    # ⭐ COLUNA CORRIGIDA/ADICIONADA: Essencial para o relatório
    categoria = db.Column(db.String(100), nullable=False) 
    paga = db.Column(db.Boolean, default=False)
    
# ----------------------------------------------------
# 3. CONTEXT PROCESSORS E FILTROS DO JINJA
# ----------------------------------------------------

@app.before_request
def load_user():
    g.user = session.get('nome')
    g.nivel = session.get('nivel_acesso')

@app.template_filter('to_date')
def format_date_filter(value):
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            return value
    return value.strftime('%d/%m/%Y') if hasattr(value, 'strftime') else str(value)

# 🚨 FILTRO ADICIONADO PARA CORRIGIR VALORES NA TELA
@app.template_filter('moeda')
def format_currency_filter(value):
    if value is None:
        return 'R$ 0,00'
    try:
        value_float = float(value)
        return "R$ {:,.2f}".format(value_float).replace(',', 'X').replace('.', ',').replace('X', '.')
    except:
        return value
    
# ----------------------------------------------------
# 4. DECORADORES E FUNÇÕES DE AUTENTICAÇÃO
# ----------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if not session.get('logged_in'):
            flash('Você precisa fazer login para acessar esta página.', 'error')
            return redirect(url_for('login'))
        return view(**kwargs)
    return wrapped_view

def admin_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if session.get('nivel_acesso') != 'ADMIN':
            flash('Acesso negado: Apenas administradores podem acessar esta página.', 'error')
            return redirect(url_for('index'))
        return view(**kwargs)
    return wrapped_view

# ----------------------------------------------------
# 4.1. FUNÇÃO AUXILIAR (NOVA)
# ----------------------------------------------------

def clean_currency_value(value_str):
    """Limpa uma string de moeda (ex: 'R$ 1.200,50') e retorna um float."""
    if not value_str:
        return 0.00
        
    # Remove R$ e espaços
    cleaned = value_str.replace('R$', '').replace(' ', '').strip()

    # 1. Remove o ponto (separador de milhar)
    cleaned = cleaned.replace('.', '')
    
    # 2. Substitui a vírgula (separador decimal) por ponto
    cleaned = cleaned.replace(',', '.')
    
    try:
        # Tenta converter para float (ex: "1200.50" -> 1200.5)
        return float(cleaned)
    except ValueError:
        return 0.00

def calcula_valor_total_servico(servico_id):
    """Calcula o valor total do serviço somando os valores de todos os itens associados."""
    # A sua implementação desta função já estava correta, apenas repetindo aqui para contexto.
    itens = ItemServico.query.filter_by(servico_id=servico_id).all()
    valor_total = sum(item.valor for item in itens)
    return valor_total

def atualiza_status_pagamento(servico):
    """Atualiza o saldo pendente e o status de pagamento de um objeto Servico."""
    
    # 1. Calcula Saldo Pendente
    servico.saldo_pendente = servico.valor_total - servico.valor_recebido
    
    # 2. Define o Status de Pagamento
    if servico.valor_total <= 0.01:
        servico.status_pagamento = 'Não Cobrado'
    elif servico.saldo_pendente <= 0.01:
        servico.status_pagamento = 'Pago'
    elif servico.valor_recebido > 0.01:
        servico.status_pagamento = 'Parcial'
    else:
        servico.status_pagamento = 'A Cobrar'

    return servico


# ----------------------------------------------------
# 5. ROTAS DE LOGIN/LOGOUT
# ----------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_id = request.form['login']
        senha = request.form['senha']
        
        usuario = Usuario.query.filter_by(login=login_id).first()
        
        if usuario and usuario.check_senha(senha):
            session['logged_in'] = True
            session['user_id'] = usuario.id
            session['nome'] = usuario.nome
            session['nivel_acesso'] = usuario.nivel_acesso
            flash(f'Bem-vindo(a), {usuario.nome}!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Login ou senha incorretos.', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Você saiu do sistema.', 'info')
    return redirect(url_for('login'))

# ----------------------------------------------------
# 6. ROTAS DO DASHBOARD
# ----------------------------------------------------

@app.route('/')
@app.route('/index')
@login_required
def index():
    servicos_andamento = Servico.query.filter(
        Servico.status_processo.in_(['Em Andamento', 'Aguardando Retirada'])
    ).count()

    total_clientes = Cliente.query.count()
    
    total_a_receber_obj = db.session.query(
        func.sum(Servico.saldo_pendente)
    ).filter(
        Servico.status_pagamento.in_(['A Cobrar', 'Parcial'])
    ).scalar()
    total_a_receber = total_a_receber_obj if total_a_receber_obj is not None else 0.0

    primeiro_dia_mes = datetime.today().replace(day=1).date()
    faturamento_mes_obj = db.session.query(
        func.sum(MovimentacaoCaixa.valor)
    ).filter(
        MovimentacaoCaixa.tipo == 'Entrada',
        MovimentacaoCaixa.data >= primeiro_dia_mes
    ).scalar()
    faturamento_mes = faturamento_mes_obj if faturamento_mes_obj is not None else 0.0

    servicos_recentes = Servico.query.join(Cliente).with_entities(
        Servico.id, Servico.tipo_servico, Servico.status_processo, Cliente.nome.label('cliente')
    ).order_by(Servico.data_servico.desc()).limit(5).all()

    return render_template(
        'index.html',
        servicos_andamento=servicos_andamento,
        total_clientes=total_clientes,
        total_a_receber=total_a_receber,
        faturamento_mes=faturamento_mes,
        servicos_recentes=servicos_recentes
    )

# ----------------------------------------------------
# 7. ROTAS DE CLIENTES
# ----------------------------------------------------

@app.route('/clientes/cadastro', methods=['GET', 'POST'])
@login_required
def cliente_cadastro():
    if request.method == 'POST':
        try:
            novo_cliente = Cliente(
                nome=request.form['nome'],
                cpf_cnpj=request.form['cpf_cnpj'],
                telefone=request.form.get('telefone'),
                email=request.form.get('email'),
                endereco=request.form.get('endereco')
            )
            db.session.add(novo_cliente)
            db.session.commit()
            flash('Cliente cadastrado com sucesso!', 'success')
            return redirect(url_for('clientes_lista'))
        except IntegrityError:
            db.session.rollback()
            flash('CPF/CNPJ já cadastrado no sistema.', 'error')
        except Exception as e:
            flash(f'Erro ao cadastrar cliente: {e}', 'error')
            
    return render_template('cliente_cadastro.html')

@app.route('/clientes/lista')
@login_required
def clientes_lista():
    clientes = Cliente.query.order_by(Cliente.id.desc()).all()
    return render_template('clientes_lista.html', clientes=clientes)
    
@app.route('/clientes/editar/<int:cliente_id>', methods=['GET', 'POST'])
@login_required
def cliente_edicao(cliente_id):
    cliente = Cliente.query.get_or_404(cliente_id)
    
    if request.method == 'POST':
        try:
            cliente.nome = request.form['nome']
            cliente.cpf_cnpj = request.form['cpf_cnpj']
            cliente.telefone = request.form.get('telefone')
            cliente.email = request.form.get('email')
            cliente.endereco = request.form.get('endereco') 
            
            db.session.commit()
            flash(f'Cliente "{cliente.nome}" atualizado com sucesso!', 'success')
            return redirect(url_for('clientes_lista'))
            
        except IntegrityError:
            db.session.rollback()
            flash('Erro: CPF/CNPJ já cadastrado para outro cliente.', 'error')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar cliente: {e}', 'error')
            
    return render_template('clientes_edicao.html', cliente=cliente)

# ----------------------------------------------------
# 8. ROTAS DE SERVIÇOS
# ----------------------------------------------------

# ROTA MODIFICADA: Utiliza a função auxiliar atualiza_status_pagamento
@app.route('/servicos/cadastro', methods=['GET', 'POST'])
@login_required
def servicos_cadastro_v3():
    # [SEU CÓDIGO PERMANECE INALTERADO DAQUI EM DIANTE]
    clientes = Cliente.query.order_by(Cliente.nome).all()
    today = date.today().isoformat()

    if request.method == 'POST':
        try:
            cliente_id = request.form.get('cliente_id')
            # Garante que tipo/placa/detalhes não sejam None
            tipo = request.form.get('tipo_servico', '').strip()
            placa = request.form.get('placa_veiculo', '').strip()
            detalhes = request.form.get('detalhes', '').strip()

            # ⭐ CORREÇÃO APLICADA: Usa clean_currency_value para segurança
            valor_total_float = clean_currency_value(request.form.get('valor_total', '0,00'))
            valor_recebido_float = clean_currency_value(request.form.get('valor_recebido_inicial', '0,00'))

            # VALIDAÇÃO OBRIGATÓRIA (Backend)
            if not cliente_id:
                flash('Selecione o Cliente é obrigatório.', 'error')
                return redirect(request.url)
            if not tipo:
                flash('O campo "Tipo de Serviço" é obrigatório.', 'error')
                return redirect(request.url)
            if not placa:
                flash('O campo "Placa do Veículo" é obrigatório.', 'error')
                return redirect(request.url)
            # FIM DA VALIDAÇÃO

            data_servico_obj = datetime.strptime(request.form.get('data_servico'), '%Y-%m-%d').date()
            data_vencimento_str = request.form.get('data_vencimento')
            data_vencimento_obj = None
            if data_vencimento_str:
                data_vencimento_obj = datetime.strptime(data_vencimento_str, '%Y-%m-%d').date()

            novo_servico = Servico(
                cliente_id=cliente_id,
                tipo_servico=tipo,
                detalhes=detalhes,
                placa_veiculo=placa,
                data_servico=data_servico_obj,
                data_vencimento=data_vencimento_obj,
                valor_total=valor_total_float,
                valor_recebido=valor_recebido_float,
                saldo_pendente=0.0,  # Valor temporário
                status_processo='Pendente',
                status_pagamento='A Cobrar'  # Valor temporário
            )

            # ⭐ CORREÇÃO APLICADA: Usa a função centralizada para definir saldo e status
            atualiza_status_pagamento(novo_servico)
            db.session.add(novo_servico)

            # Adiciona Movimentação de Caixa SE houver recebimento inicial
            if valor_recebido_float > 0.01:
                pagamento_data = data_servico_obj
                pagamento_descricao = f"Recebimento Inicial - Serviço #{novo_servico.id} - {tipo}"
                nova_movimentacao = MovimentacaoCaixa(
                    tipo='Entrada',
                    valor=valor_recebido_float,
                    data=pagamento_data,
                    descricao=pagamento_descricao,
                    referencia_id=novo_servico.id,  # Referencia_id deve ser o ID do serviço
                    referencia_tipo='Servico'
                )
                db.session.add(nova_movimentacao)

            db.session.commit()

            # ✅ ALTERAÇÃO SOLICITADA:
            # Agora redireciona diretamente para a lista de serviços
            flash(f'Serviço "{tipo}" cadastrado com sucesso.', 'success')
            return redirect(url_for('servicos_filtros'))

        except ValueError as ve:
            db.session.rollback()
            flash(f'Erro de formato. Verifique se as datas e os valores monetários foram preenchidos corretamente.', 'error')
            return redirect(request.url)

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar serviço: {e}', 'error')

    return render_template('servicos_cadastro_v3.html', clientes=clientes, today=today)


# ROTA MODIFICADA E EXPANDIDA
@app.route('/servico/atualizar/<int:servico_id>', methods=['GET', 'POST'])
@login_required
def atualizar_status_servico(servico_id):
    servico = Servico.query.get_or_404(servico_id)
    cliente = Cliente.query.get(servico.cliente_id)
    itens_servico = ItemServico.query.filter_by(servico_id=servico_id).order_by(ItemServico.id.asc()).all()
    
    # ✅ ADICIONADO: lista de todos os clientes
    clientes = Cliente.query.order_by(Cliente.nome).all()

    status_opcoes = ["Pendente", "Em Andamento", "Aguardando Retirada", "Concluído", "Cancelado"]

    # ------------------------------------------
    # Lógica de POST (Atualização de dados)
    # ------------------------------------------
    if request.method == 'POST':
        try:
            # ✅ CORREÇÃO: Apenas atualiza status e detalhes (sem sobreposições)
            servico.status_processo = request.form.get('status_processo')
            servico.detalhes = request.form.get('detalhes')

            # ------------------------------------------
            # Atualizar Status de Pagamento e Salvar
            # ------------------------------------------
            atualiza_status_pagamento(servico)
            db.session.commit()
            flash('Status e observações atualizados com sucesso!', 'success')
            return redirect(url_for('servicos_filtros'))

        except ValueError:
            db.session.rollback()
            flash('Erro de conversão de valor ou data. Verifique os campos informados.', 'error')
            return redirect(url_for('atualizar_status_servico', servico_id=servico.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro interno ao atualizar o serviço. Detalhes: {e}', 'error')
            return redirect(url_for('servicos_filtros'))


 # ------------------------------------------
    # Lógica de GET (Renderizar página)
    # ------------------------------------------
    data_entrada_formatada = servico.data_servico.strftime('%Y-%m-%d') if servico.data_servico else date.today().strftime('%Y-%m-%d')
    data_vencimento_formatada = servico.data_vencimento.strftime('%Y-%m-%d') if servico.data_vencimento else ''

    return render_template(
        'atualizar_status_servico.html',
        servico=servico,
        cliente=cliente,
        clientes=clientes,
        itens_servico=itens_servico,
        status_opcoes=status_opcoes,
        today=date.today().strftime('%Y-%m-%d'),
        data_entrada=data_entrada_formatada,
        data_vencimento=data_vencimento_formatada
    )

# ----------------------------------------------------
# ROTA PARA EXCLUSÃO DE SERVIÇO
# ----------------------------------------------------
@app.route('/servico/excluir/<int:servico_id>', methods=['POST'])
@login_required
@admin_required
def excluir_servico(servico_id):
    try:
        # Tenta buscar o serviço ou retorna 404
        servico = Servico.query.get_or_404(servico_id)
        
        # 1️⃣ Excluir itens relacionados. Usamos synchronize_session='fetch' para garantir que os objetos excluídos 
        # sejam removidos corretamente da sessão do SQLAlchemy antes do commit.
        ItemServico.query.filter_by(servico_id=servico_id).delete(synchronize_session='fetch')
        
        # 2️⃣ Excluir movimentações de caixa relacionadas
        movimentacoes_servico = MovimentacaoCaixa.query.filter(
            MovimentacaoCaixa.referencia_id == servico_id,
            MovimentacaoCaixa.referencia_tipo == 'Servico'
        ).all()

        # 3️⃣ Excluir o serviço
        db.session.delete(servico)
        
        # 4️⃣ Confirma todas as exclusões no banco de dados
        db.session.commit()
        
        # Mensagem de sucesso
        flash(f'Serviço #{servico_id} excluído com sucesso!', 'success')
        
    except Exception as e:
        # Se ocorrer um erro, desfazemos todas as operações (rollback)
        db.session.rollback()
        # Mensagem de erro
        flash(f'Erro ao excluir serviço: {e}', 'error')
    
    # Redireciona para a página de serviços (após sucesso ou falha)
    return redirect(url_for('servicos_filtros'))



# ----------------------------------------------------
# 9. ROTAS DE SERVIÇOS COM FILTROS (Sem alterações necessárias)
# ----------------------------------------------------
@app.route('/servicos/filtros', methods=['GET'])
@login_required
def servicos_filtros():
    # [SEU CÓDIGO PERMANECE INALTERADO]
    clientes_list = Cliente.query.order_by(Cliente.nome).all()
    filtro_status = request.args.get('status', 'todos')
    filtro_cliente = request.args.get('cliente', '')
    filtro_placa = request.args.get('placa', '')
    filtro_data_servico = request.args.get('data_servico', '')
    filtro_data_fim = request.args.get('data_fim', '')

    query = Servico.query.join(Cliente, Servico.cliente_id == Cliente.id).with_entities(
        Servico.id,
        Servico.tipo_servico,
        Servico.data_servico,
        Servico.valor_total,
        Servico.valor_recebido,
        Servico.saldo_pendente,
        Servico.status_processo,
        Servico.status_pagamento,
        Cliente.nome.label('cliente')
    ).order_by(Servico.id.desc())

    if filtro_status != 'todos' and filtro_status:
        query = query.filter(Servico.status_processo == filtro_status)
    if filtro_cliente:
        try:
            cliente_id = int(filtro_cliente)
            query = query.filter(Servico.cliente_id == cliente_id)
        except ValueError:
            pass
    if filtro_placa:
        query = query.filter(Servico.placa_veiculo.ilike(f'%{filtro_placa}%'))
    if filtro_data_servico:
        query = query.filter(Servico.data_servico >= filtro_data_servico)
    if filtro_data_fim:
        query = query.filter(Servico.data_servico <= filtro_data_fim)

    servicos_filtrados = query.all()
    status_opcoes = ['Pendente', 'Em Andamento', 'Aguardando Retirada', 'Concluído', 'Cancelado']

    return render_template(
        'servicos_filtros.html',
        servicos=servicos_filtrados,
        clientes=clientes_list,
        filtro_status=filtro_status,
        filtro_cliente=filtro_cliente,
        filtro_placa=filtro_placa,
        filtro_data_servico=filtro_data_servico,
        filtro_data_fim=filtro_data_fim,
        status_opcoes=status_opcoes
    )

# ----------------------------------------------------
# 10. ROTAS DE PAGAMENTO, CAIXA E DESPESAS
# ----------------------------------------------------
@app.route('/servicos/pagamento', methods=['GET', 'POST'])
@login_required

def processar_pagamento():
    from datetime import date, datetime 
    
    # ⚠️ Assumindo: MovimentosFinanceiros (MovimentacaoCaixa), db, Cliente, Servico,
    # clean_currency_value, atualiza_status_pagamento, render_template estão disponíveis.
    
    today_iso = date.today().isoformat()

    # --- Filtragem GET ---
    cliente_id = request.args.get('cliente_id')
    placa = request.args.get('placa')
    data_filtro = request.args.get('data')
    
    # 1. QUERY BASE
    # ✅ Reincorpora a placa e calcula o saldo dinamicamente
    query = Servico.query.join(Cliente).with_entities(
        Servico.id,
        Servico.data_servico,
        Servico.tipo_servico,
        Servico.placa_veiculo.label('placa'),  # Placa reincorporada
        Servico.valor_total,
        Servico.valor_recebido,
        (Servico.valor_total - Servico.valor_recebido).label('saldo_pendente'),  # Saldo calculado
        Servico.status_pagamento,
        Servico.cliente_id,
        Cliente.nome.label('cliente')
    )

    # 2. APLICAÇÃO DOS FILTROS
    if cliente_id and cliente_id.isdigit():
        try:
            cliente_id_int = int(cliente_id)
            query = query.filter(Servico.cliente_id == cliente_id_int)
        except ValueError:
            pass 
            
    # ✅ Reincorpora o filtro de placa (que agora existe na tabela)
    if placa and placa.strip():
        query = query.filter(Servico.placa_veiculo == placa.strip()) 
        
    if data_filtro:
        try:
            dt = datetime.strptime(data_filtro, '%Y-%m-%d').date()
            query = query.filter(Servico.data_servico == dt)
        except ValueError:
            pass

    # 3. EXECUTA A BUSCA PARA PREENCHER A TABELA
    # ✅ Busca por saldo (calculado) maior que 0.01
    servicos_rows = query.filter(
        (Servico.valor_total - Servico.valor_recebido) > 0.01
    ).order_by(Servico.data_servico.desc()).all()
    
    servicos_filtrados = [dict(row._mapping) for row in servicos_rows]


    # 4. BUSCA PARA POPULAR DROPDOWNS (PLACA DINÂMICA)
    # ✅ Lógica de placas reincorporada
    clientes = Cliente.query.order_by(Cliente.nome).all()
    placas_por_cliente = {}
    placas_gerais_set = set()

    for c in clientes:
        placas_query = Servico.query.filter(
            Servico.cliente_id == c.id, 
            Servico.placa_veiculo.isnot(None)
        ).with_entities(Servico.placa_veiculo).distinct().all()
        
        placas_do_cliente = [p[0] for p in placas_query if p[0] and p[0].strip()]
        
        placas_por_cliente[c.id] = placas_do_cliente
        placas_gerais_set.update(placas_do_cliente)
    
    placas = sorted(list(placas_gerais_set)) 


    # 5. POST → REGISTRO DE PAGAMENTO 
    if request.method == 'POST':
        servico_id = request.form.get('servico_id')
        
        if not servico_id:
            flash('Selecione um serviço antes de confirmar o pagamento.', 'error')
            return redirect(url_for('processar_pagamento')) 

        servico = Servico.query.get_or_404(servico_id)

        try:
            valor_recebido_novo = clean_currency_value(request.form.get('valor_pago', '0,00'))
            metodo_pagamento = request.form.get('metodo_pagamento', 'PIX')
            
            data_pagamento_str = request.form.get('data_pagamento', today_iso)
            data_pagamento = datetime.strptime(data_pagamento_str, '%Y-%m-%d').date()

            # ... (Validação de valor/saldo omitida por brevidade) ...

            servico.valor_recebido = (servico.valor_recebido or 0.0) + valor_recebido_novo
            
            atualiza_status_pagamento(servico) # Atualiza status no banco

            descricao = f'Pagamento serviço #{servico.id} - {servico.tipo_servico} (Método: {metodo_pagamento})'
            
            # 🚀 CORREÇÃO PRINCIPAL: Assegurar o vínculo MovimentacaoCaixa <-> Servico
            movimentacao = MovimentacaoCaixa(
                data=data_pagamento,
                tipo='ENTRADA', 
                valor=valor_recebido_novo,
                descricao=descricao,
                # ESTAS LINHAS SÃO ESSENCIAIS PARA O FLUXO DE CAIXA
                referencia_id=servico.id,      # ID do Serviço sendo pago
                referencia_tipo='Servico'      # Indica que a referência é um Serviço
            )
            db.session.add(movimentacao)
            db.session.commit()

            flash('Pagamento registrado com sucesso!', 'success')
            # 💡 Sugestão: Redirecionar para o relatório para verificação imediata
            return redirect(url_for('relatorio_fluxo_caixa')) 

        except ValueError:
            db.session.rollback()
            flash('Valor ou data do pagamento inválidos.', 'error')
            return redirect(url_for('processar_pagamento'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao processar pagamento: {e}', 'error')
            return redirect(url_for('processar_pagamento'))

    # 6. RETORNO GET (RENDER TEMPLATE)
    return render_template(
        'pagamento_form.html',
        servicos_filtrados=servicos_filtrados,
        clientes=clientes,
        placas=placas,
        placas_por_cliente=placas_por_cliente, 
        selected_cliente_id=cliente_id or '', 
        selected_placa=placa or '',
        today=today_iso
    )

# ----------------------------------------------------
# ROTA 10.2 - Visualizar caixa (Corrigida para ignorar registros órfãos)
# ----------------------------------------------------
@app.route('/caixa')
@login_required
# ATENÇÃO: admin_required é um decorator que deve ser definido
# @admin_required 
def visualizar_caixa():
    # Importações necessárias (assumindo que datetime e os modelos estão disponíveis)
    from datetime import datetime
    # Supondo que MovimentacaoCaixa e Servico já foram importados no escopo global
    
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    query = MovimentacaoCaixa.query

    # --- Aplicação dos Filtros de Data ---
    if start_date:
        try:
            sd = datetime.strptime(start_date, '%Y-%m-%d').date()
            query = query.filter(MovimentacaoCaixa.data >= sd)
        except Exception:
            pass
    if end_date:
        try:
            ed = datetime.strptime(end_date, '%Y-%m-%d').date()
            query = query.filter(MovimentacaoCaixa.data <= ed)
        except Exception:
            pass
    # ------------------------------------

    movimentos = query.order_by(MovimentacaoCaixa.data.desc(), MovimentacaoCaixa.id.desc()).all()

    extrato = []
    total_entradas = 0.0
    total_saidas = 0.0

    for m in movimentos:
        
        # 🟢 CORREÇÃO: VERIFICAÇÃO DE REGISTRO ÓRFÃO
        if m.referencia_tipo == 'Servico':
            # Se a movimentação é de um Serviço, checamos se o Serviço ainda existe.
            try:
                # Se o Servico não for encontrado, ele não será adicionado ao extrato.
                servico_existente = Servico.query.get(m.referencia_id)
                if not servico_existente:
                    continue # Pula este movimento (ignora o órfão)
            except Exception:
                # Em caso de erro de consulta, para segurança, também pulamos.
                continue
        # --------------------------------------

        tipo_label = 'ENTRADA' if (m.tipo and m.tipo.lower() == 'entrada') else 'SAÍDA'
        valor = float(m.valor or 0.0)
        
        if tipo_label == 'ENTRADA':
            total_entradas += valor
        else:
            total_saidas += valor

        categoria = m.referencia_tipo or ''

        extrato.append({
            'data': m.data,
            'tipo': tipo_label,
            'descricao': m.descricao or '',
            'valor': valor,
            'categoria': categoria
        })

    saldo_geral = total_entradas - total_saidas

    return render_template(
        'visualizar_caixa.html',
        extrato=extrato,
        total_entradas=total_entradas,
        total_despesas=total_saidas,
        saldo_geral=saldo_geral,
        start_date=start_date,
        end_date=end_date
    )

# ----------------------------------------------------
# ROTA 10.3 - Registrar despesa
# ----------------------------------------------------
@app.route('/despesas/registro', methods=['GET', 'POST'])
@login_required
# @admin_required
def despesa_form():
    today_iso = date.today().isoformat()

    if request.method == 'POST':
        try:
            descricao = request.form.get('descricao', '').strip()
            # ⭐ CORREÇÃO APLICADA: Usa clean_currency_value
            valor = clean_currency_value(request.form.get('valor', '0,00')) 
            data_str = request.form.get('data_pagamento', request.form.get('data', ''))
            
            # ✅ NOVO: Coleta a categoria do formulário
            categoria = request.form.get('categoria', 'OUTRAS') 

            if not descricao or valor <= 0:
                flash('Preencha a descrição e informe um valor válido.', 'error')
                return redirect(url_for('despesa_form'))

            data_obj = datetime.strptime(data_str, '%Y-%m-%d').date() if data_str else date.today()

            # 1. 🟢 CRIAÇÃO DO OBJETO DESPESA (Registro Histórico)
            nova_despesa = Despesa(
                data=data_obj,
                valor=valor,
                descricao=descricao, # Usa a descrição original
                categoria=categoria, # Argumento obrigatório
                paga=True # Assumindo que o registro aqui significa que foi paga
            )
            db.session.add(nova_despesa)
            db.session.flush() # Obtém o ID da despesa (nova_despesa.id) antes do commit
            
            # 2. 🟢 CRIAÇÃO DO OBJETO MOVIMENTACAOCAIXA (Movimento Financeiro)
            # Usa o ID da Despesa como referência
            movimentacao = MovimentacaoCaixa(
                data=data_obj,
                tipo='Saída',
                valor=valor,
                # Ajusta a descrição para clareza no extrato
                descricao=f'Despesa: {descricao} (Categoria: {categoria})', 
                referencia_id=nova_despesa.id, # Vincula a Despesa recém-criada
                referencia_tipo='Despesa'
            )
            db.session.add(movimentacao)
            
            db.session.commit()

            flash('Despesa registrada com sucesso!', 'success')
            return redirect(url_for('visualizar_caixa'))

        except ValueError:
            db.session.rollback()
            flash('Erro no formato da data ou valor.', 'error')
            return redirect(url_for('despesa_form'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao registrar despesa: {e}', 'error')
            return redirect(url_for('despesa_form'))

    return render_template('despesa_form.html', today=today_iso)

# ----------------------------------------------------
# ROTA 10.4 - Histórico de movimentações (Sem alterações necessárias)
# ----------------------------------------------------
@app.route('/caixa/historico')
@login_required
# @admin_required
def historico_caixa():
    registros = MovimentacaoCaixa.query.order_by(MovimentacaoCaixa.data.desc(), MovimentacaoCaixa.id.desc()).all()
    total_entradas = sum(r.valor for r in registros if (r.tipo and r.tipo.lower() == 'entrada'))
    total_saidas = sum(r.valor for r in registros if not (r.tipo and r.tipo.lower() == 'entrada'))
    saldo_atual = total_entradas - total_saidas

    historico = []
    for r in registros:
        historico.append({
            'data': r.data,
            'tipo': 'ENTRADA' if (r.tipo and r.tipo.lower() == 'entrada') else 'SAÍDA',
            'descricao': r.descricao,
            'valor': float(r.valor or 0.0),
            'categoria': r.referencia_tipo or ''
        })

    return render_template(
        'historico_caixa.html',
        registros=historico,
        total_entradas=total_entradas,
        total_saidas=total_saidas,
        saldo_atual=saldo_atual
    )
# ----------------------------------------------------
# ROTA 10.5 - Relatórios Débitos (Contas a Receber) - CORRIGIDA
# ----------------------------------------------------

@app.route("/relatorios/debitos", methods=["GET"])
@login_required
def relatorio_debitos():
    from datetime import datetime
    from sqlalchemy import func, and_
    
    # --- 1. Captura e Tratamento dos Filtros ---
    cliente_id_str = request.args.get("cliente_id")
    placa = request.args.get("placa")
    data_inicio_str = request.args.get("data_inicio")
    data_fim_str = request.args.get("data_fim")

    # Função auxiliar para parsear datas
    def parse_date(date_str):
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else None
        except ValueError:
            return None

    data_inicio = parse_date(data_inicio_str)
    data_fim = parse_date(data_fim_str)
    
    # Tentativa de conversão para int, se não for vazio
    cliente_id = int(cliente_id_str) if cliente_id_str and cliente_id_str.isdigit() else None
    
    # --- 2. Construção da Consulta Base (Somente Débitos) ---
    # A condição principal: Valor Total > Valor Recebido
    query = Servico.query.filter(Servico.valor_total > Servico.valor_recebido)
    
    # --- 3. Aplicação dos Filtros Adicionais ---
    
    # Filtro de Cliente
    if cliente_id:
        query = query.filter(Servico.cliente_id == cliente_id)

    # Filtro de Placa (Busca por 'like' para flexibilidade)
    if placa:
        # Garante que a busca por placa seja insensível a maiúsculas/minúsculas
        query = query.filter(func.lower(Servico.placa_veiculo).like(f"%{placa.lower()}%"))

    # Filtro de Data Inicial
    if data_inicio:
        query = query.filter(Servico.data_servico >= data_inicio)

    # Filtro de Data Final
    if data_fim:
        query = query.filter(Servico.data_servico <= data_fim)

    # --- 4. Execução da Consulta e Preparação dos Dados ---
    debitos_raw = query.order_by(Servico.data_servico.asc()).all()
    
    # 4.1. Preparar a lista final de débitos com o saldo calculado e o nome do cliente
    debitos = []
    total_debitos = 0.0

    for s in debitos_raw:
        saldo_devedor = s.valor_total - s.valor_recebido
        
        # Como a query já filtrou (valor_total > valor_recebido), saldo_devedor deve ser > 0.
        # Mas vamos incluir o cálculo e dados formatados para o template.
        
        debitos.append({
            'id': s.id,
            'cliente_nome': s.cliente.nome,
            'data_servico': s.data_servico,
            'placa_veiculo': s.placa_veiculo,
            'tipo_servico': s.tipo_servico,
            'valor_total': s.valor_total,
            'valor_recebido': s.valor_recebido,
            'saldo_devedor': saldo_devedor,
        })
        total_debitos += saldo_devedor
        
    # --- 5. Dados Auxiliares e Renderização ---
    clientes = Cliente.query.order_by(Cliente.nome).all()
    
    # Prepara o nome do cliente para o cabeçalho de impressão, se filtrado
    selected_cliente_nome = next((c.nome for c in clientes if c.id == cliente_id), None)

    return render_template(
        "relatorio_debitos.html",
        clientes=clientes,
        debitos=debitos,
        total_debitos=total_debitos,
        # Variáveis de retorno dos filtros
        selected_cliente_id=cliente_id_str,
        selected_placa=placa,
        selected_data_inicio=data_inicio_str,
        selected_data_fim=data_fim_str,
        selected_cliente_nome=selected_cliente_nome,
        today=datetime.now().date()
    )


# ----------------------------------------------------
# ROTA 10.6 - Relatorio de Despesas - CORRIGIDA
# ----------------------------------------------------
@app.route('/relatorios/despesas', methods=['GET'])
@login_required
def relatorio_despesas():
    from datetime import datetime
    
    # Parâmetros de Filtro
    data_inicio_str = request.args.get('data_inicio')
    data_fim_str = request.args.get('data_fim')
    categoria_filtro = request.args.get('categoria')
    
    # 1. Montagem da Consulta Base
    query = Despesa.query
    
    # 2. Aplicação de Filtros
    if categoria_filtro and categoria_filtro.strip():
        query = query.filter(Despesa.categoria == categoria_filtro)
            
    try:
        if data_inicio_str:
            data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
            query = query.filter(Despesa.data >= data_inicio)
        
        if data_fim_str:
            data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d').date()
            query = query.filter(Despesa.data <= data_fim)
    except ValueError:
        flash('Formato de data inválido.', 'error')
        
    # 3. Execução da Consulta e Ordenação
    despesas = query.order_by(
        Despesa.data.desc() 
    ).all()

    try:
        # Assumindo que db.session e Despesa estão acessíveis
        categorias_unicas = db.session.query(Despesa.categoria).distinct().all()
        categorias = sorted([c[0] for c in categorias_unicas if c[0]])
    except Exception:
        categorias = []
    
    total_despesas = sum(d.valor for d in despesas)
    
    # CORREÇÃO: Chamando um template 'relatorio_despesas.html' (assumindo que seja esse o nome)
    return render_template(
        'relatorio_despesas.html', # Certifique-se de que este template existe na raiz 'templates/'
        despesas=despesas,
        categorias=categorias,
        selected_categoria=categoria_filtro,
        selected_data_inicio=data_inicio_str,
        selected_data_fim=data_fim_str,
        total_despesas=total_despesas
    )

# ----------------------------------------------------
# ROTA 10.7 - Relatório Gerencial / Faturamento (CORRIGIDA)
# ----------------------------------------------------
@app.route("/relatorios/fluxo_caixa", methods=["GET", "POST"])
@login_required
def relatorio_fluxo_caixa():
    from datetime import datetime
    from sqlalchemy import func

    # --- 1. Captura dos filtros do formulário ---
    data_inicio = request.form.get("data_inicio") or request.args.get("data_inicio")
    data_fim = request.form.get("data_fim") or request.args.get("data_fim")
    cliente_id = request.form.get("cliente_id") or request.args.get("cliente_id")
    tipo_servico = request.form.get("tipo_servico") or request.args.get("tipo_servico")

    # --- 2. Conversão segura das datas ---
    def parse_date(date_str):
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else None
        except Exception:
            return None

    data_inicio = parse_date(data_inicio)
    data_fim = parse_date(data_fim)

    # --- 3. Consultas principais ---
    query_servicos = Servico.query
    query_mov = MovimentacaoCaixa.query 
    query_despesas = Despesa.query

    # --- 4. Aplicação dos filtros ---
    
    if data_inicio:
        query_servicos = query_servicos.filter(Servico.data_servico >= data_inicio)
        query_mov = query_mov.filter(MovimentacaoCaixa.data >= data_inicio)
        query_despesas = query_despesas.filter(Despesa.data >= data_inicio)

    if data_fim:
        query_servicos = query_servicos.filter(Servico.data_servico <= data_fim)
        query_mov = query_mov.filter(MovimentacaoCaixa.data <= data_fim)
        query_despesas = query_despesas.filter(Despesa.data <= data_fim)

    # Filtros de Cliente e Tipo SÓ se aplicam a 'Servico'
    if cliente_id:
        query_servicos = query_servicos.filter(Servico.cliente_id == cliente_id)

    if tipo_servico:
        query_servicos = query_servicos.filter(Servico.tipo_servico == tipo_servico)

    # --- 5. Execução das consultas ---
    servicos = query_servicos.all() 
    movimentacoes_brutas = query_mov.all() 
    despesas = query_despesas.all() # Despesas avulsas filtradas por data
    
    # --- 5.1 FILTRAGEM DE MOVIMENTAÇÕES ÓRFÃS E POR CLIENTE ---
    movimentacoes = [] # Lista final de movimentos válidos
    
    for m in movimentacoes_brutas:
        # 1. Movimentos que não são de Serviço são sempre incluídos.
        if m.referencia_tipo != 'Servico':
            movimentacoes.append(m)
            continue
        
        # 2. Se for Serviço, checamos a existência (filtro de órfão)
        servico_existente = Servico.query.get(m.referencia_id)

        if servico_existente:
            # 3. Se houver filtro de cliente, verificamos se o serviço pertence a esse cliente
            if cliente_id and str(servico_existente.cliente_id) != cliente_id:
                continue
                
            movimentacoes.append(m)
        
       # --- 6. Cálculos consolidados ---
    total_clientes = len(set(s.cliente_id for s in servicos))
    total_servicos = len(servicos)

    total_faturado = sum(s.valor_total for s in servicos)
    total_recebido = sum(s.valor_recebido for s in servicos)
    total_pendente = total_faturado - total_recebido

    # Cálculos a partir da lista 'movimentacoes' FILTRADA
    # 🚀 CORREÇÃO: Incluir movimentos que são do tipo 'entrada' OU que são referenciados a um Serviço.
    total_entradas = sum(m.valor for m in movimentacoes if m.tipo and (
        'entrada' in m.tipo.lower() or m.referencia_tipo == 'Servico'
    ))
    
    total_saidas_caixa = sum(m.valor for m in movimentacoes if m.tipo and 'saida' in m.tipo.lower())
    
    # ✅ 1. Calcular o total de Despesas da tabela Despesa
    total_despesas_avulsas = sum(d.valor for d in despesas)

    # ✅ 2. O Total de Saídas (Soma das Saídas do Caixa + Despesas Avulsas)
    total_saidas_geral = total_saidas_caixa + total_despesas_avulsas 

    # ✅ 3. O saldo líquido subtrai o TOTAL de saídas
    saldo_liquido = total_entradas - total_saidas_geral

    # --- 7. Dados auxiliares para filtros ---
    clientes = Cliente.query.all()
    tipos_servicos = [t[0] for t in db.session.query(Servico.tipo_servico).distinct().all()]

    # --- 8. Renderização (E CORREÇÃO NA VARIÁVEL ENVIADA) ---
    return render_template(
        "relatorio_faturamento.html",
        clientes=clientes,
        tipos_servicos=tipos_servicos,
        servicos=servicos,
        movimentacoes=movimentacoes, 
        despesas=despesas,
        total_clientes=total_clientes,
        total_servicos=total_servicos,
        total_faturado=total_faturado,
        total_recebido=total_recebido,
        total_pendente=total_pendente,
        total_entradas=total_entradas,
        # ✅ Enviar o total CORRETO de saídas (caixa + despesas)
        total_saidas=total_saidas_geral, 
        saldo_liquido=saldo_liquido,
        data_inicio=data_inicio.strftime("%Y-%m-%d") if data_inicio else "",
        data_fim=data_fim.strftime("%Y-%m-%d") if data_fim else "",
        cliente_id=int(cliente_id) if cliente_id and cliente_id.isdigit() else None,
        tipo_servico=tipo_servico or ""
    )

# ----------------------------------------------------
# ROTA 10.8 - Exportar Relatório de Débitos em PDF
# ----------------------------------------------------
@app.route("/exportar_debitos_pdf", methods=["GET"])
@login_required
def exportar_debitos_pdf():
    from datetime import datetime
    from io import BytesIO
    from reportlab.lib.pagesizes import A4, landscape 
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from sqlalchemy import func
    
    # --- Cores Personalizadas (Detran RS / GOV-RS Estilo) ---
    COR_DETRAN_ACCENT = colors.HexColor('#FF6600')      # Laranja para destaque (Título)
    COR_DETRAN_HEADER_BG = colors.HexColor('#333333')   # Cinza Grafite Escuro (Fundo da Tabela)
    COR_DETRAN_TEXT = colors.HexColor('#333333')        # Cinza Grafite para texto
    
    # --- 1. Captura e Tratamento dos Filtros ---
    cliente_id_str = request.args.get("cliente_id")
    placa = request.args.get("placa")
    data_inicio_str = request.args.get("data_inicio")
    data_fim_str = request.args.get("data_fim")
    
    # Função auxiliar para parsear datas
    def parse_date(date_str):
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else None
        except ValueError:
            return None

    data_inicio = parse_date(data_inicio_str)
    data_fim = parse_date(data_fim_str)
    cliente_id = int(cliente_id_str) if cliente_id_str and cliente_id_str.isdigit() else None
    
    # --- 2. Construção da Consulta Base (Somente Débitos) ---
    query = Servico.query.join(Cliente).filter(Servico.valor_total > Servico.valor_recebido).with_entities(
        Servico.id,
        Servico.data_servico,
        Servico.placa_veiculo,
        Servico.tipo_servico,
        Servico.valor_total,
        Servico.valor_recebido,
        (Servico.valor_total - Servico.valor_recebido).label('saldo_devedor'),
        Servico.status_processo, 
        Cliente.nome.label('cliente_nome'),
        Cliente.cpf_cnpj.label('cliente_doc'),
        Cliente.id.label('cliente_id')
    )

    # --- 3. Aplicação dos Filtros Adicionais ---
    if cliente_id:
        query = query.filter(Servico.cliente_id == cliente_id)
        
    if placa:
        query = query.filter(func.lower(Servico.placa_veiculo).like(f"%{placa.lower()}%"))

    if data_inicio:
        query = query.filter(Servico.data_servico >= data_inicio)

    if data_fim:
        query = query.filter(Servico.data_servico <= data_fim)

    # --- 4. Execução da Consulta e Preparação dos Dados ---
    debitos_raw = query.order_by(Servico.cliente_id, Servico.data_servico.asc()).all()

    total_debitos = sum(row.saldo_devedor for row in debitos_raw)
    
    # --- Coleta de Dados do Cliente Selecionado (para Cabeçalho) ---
    selected_cliente_nome = "TODOS OS CLIENTES"
    cliente_info_extra = None
    
    if cliente_id:
        cliente_obj = Cliente.query.get(cliente_id)
        if cliente_obj:
            selected_cliente_nome = cliente_obj.nome
            cliente_info_extra = cliente_obj
        selected_cliente_doc = debitos_raw[0].cliente_doc if debitos_raw else (cliente_obj.cpf_cnpj if cliente_obj else "N/A")
    else:
        selected_cliente_doc = "N/A"

    # --- 5. Geração do PDF Formal ---
    buffer = BytesIO()
    
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), topMargin=1*cm, bottomMargin=1*cm, leftMargin=1.5*cm, rightMargin=1.5*cm)
    styles = getSampleStyleSheet()
    story = []
    
    # ⭐ MODIFICAÇÃO DE ESTILOS: 
    # 1. TitleDetran: Fonte 16 (agora será 18 no Escritório), cor Laranja, centralizado.
    # 2. NormalCentralizado: Centralizado para informações de contato.
    styles.add(ParagraphStyle(name='TitleDetran', fontSize=16, leading=20, fontName='Helvetica-Bold', alignment=1, textColor=COR_DETRAN_ACCENT, spaceAfter=18))
    styles.add(ParagraphStyle(name='NormalCentralizado', fontSize=11, leading=14, fontName='Helvetica', textColor=COR_DETRAN_TEXT, alignment=1)) # NOVO ESTILO
    styles.add(ParagraphStyle(name='TableText', fontSize=9, leading=10, fontName='Helvetica', textColor=COR_DETRAN_TEXT))
    styles.add(ParagraphStyle(name='ClientInfo', fontSize=10, leading=14, spaceAfter=6, fontName='Helvetica', textColor=COR_DETRAN_TEXT))
    styles.add(ParagraphStyle(name='ClientInfoBold', fontSize=10, leading=14, spaceAfter=6, fontName='Helvetica-Bold', textColor=COR_DETRAN_TEXT))
    styles.add(ParagraphStyle(name='NormalDetran', fontSize=11, leading=14, fontName='Helvetica', textColor=COR_DETRAN_TEXT))
    
    # --- 5.1 Cabeçalho Formal ---
    
    # ⭐ MODIFICAÇÃO PRINCIPAL: Título com tag <font size="+2"> e <u>
    title_text = f"<u><font size=\"+4\">Escritório Despachante Machado</font></u> - Demonstrativo de Débitos Pendentes"
    story.append(Paragraph(title_text, styles["TitleDetran"]))
    
    # ⭐ MODIFICAÇÃO: Informações de Contato Centralizadas
    story.append(Paragraph(f"Contato: (55) 3411-8153/ (55) 9953-6173 | machadodespachante@hotmail.com", styles["NormalCentralizado"]))
    story.append(Spacer(1, 12))

    # DADOS DETALHADOS DO CLIENTE (SE FILTRADO)
    if cliente_info_extra:
        story.append(Paragraph(f"<b>DADOS DO CLIENTE:</b>", styles['ClientInfoBold']))
        story.append(Paragraph(f"<b>Nome/Razão Social:</b> {cliente_info_extra.nome}", styles['ClientInfo']))
        story.append(Paragraph(f"<b>CPF/CNPJ:</b> {cliente_info_extra.cpf_cnpj}", styles['ClientInfo']))
        story.append(Paragraph(f"<b>Endereço:</b> {cliente_info_extra.endereco or 'Não Informado'}", styles['ClientInfo']))
        story.append(Paragraph(f"<b>Telefone:</b> {cliente_info_extra.telefone or 'Não Informado'} | <b>E-mail:</b> {cliente_info_extra.email or 'Não Informado'}", styles['ClientInfo']))
        story.append(Spacer(1, 12))
    
    # Informações do Filtro Geral
    story.append(Paragraph(f"<b>RELATÓRIO DE DÉBITOS ATUAL</b>", styles['ClientInfoBold']))
    if not cliente_info_extra:
        story.append(Paragraph(f"<b>CLIENTE:</b> {selected_cliente_nome}", styles['ClientInfo']))

    periodo = f"{data_inicio.strftime('%d/%m/%Y') if data_inicio else 'Início'} a {data_fim.strftime('%d/%m/%Y') if data_fim else 'Hoje'}"
    story.append(Paragraph(f"<b>Período Filtrado:</b> {periodo} | <b>Emissão:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles["ClientInfo"]))
    story.append(Spacer(1, 18))

    # --- 5.2 Tabela de Débitos Itemizados (Mantido) ---
    
    if cliente_id:
        tabela_debitos = [["Data", "ID", "Placa", "Serviço", "Status", "Total (R$)", "Recebido (R$)", "SALDO (R$)"]]
    else:
        tabela_debitos = [["Data", "Cliente", "Placa", "Serviço", "Status", "Total (R$)", "Recebido (R$)", "SALDO (R$)"]]
    
    
    def format_currency(value):
        return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        
    def p_text(text):
        if text is None:
            text = ''
        return Paragraph(str(text), styles['TableText'])
        
    for row in debitos_raw:
        row_data = [
            row.data_servico.strftime("%d/%m/%Y"), 
            p_text(row.placa_veiculo or 'N/A'),
            p_text(row.tipo_servico),
            p_text(row.status_processo),
            format_currency(row.valor_total),
            format_currency(row.valor_recebido),
            format_currency(row.saldo_devedor),
        ]
        
        if cliente_id:
            row_data.insert(1, str(row.id)) 
        else:
            row_data.insert(1, p_text(row.cliente_nome)) 
            
        tabela_debitos.append(row_data)
        
    # --- Criação da Tabela com Ajuste Automático ---
    t = Table(tabela_debitos, colWidths='*', repeatRows=1) 
    
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), COR_DETRAN_HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (-3, 0), (-1, -1), 'RIGHT'), 
        ('TEXTCOLOR', (-1, 1), (-1, -1), colors.red),
        ('FONTNAME', (-1, 1), (-1, -1), 'Helvetica-Bold'), 
    ]))
    story.append(t)
    story.append(Spacer(1, 18))
    
    # --- 5.3 Totalização Final (Mantido) ---
    total_data = [
        ["TOTAL PENDENTE GERAL:", format_currency(total_debitos)]
    ]
    
    total_table = Table(total_data, colWidths='*')
    total_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TEXTCOLOR', (0, 0), (0, 0), COR_DETRAN_TEXT), 
        ('TEXTCOLOR', (1, 0), (1, 0), colors.red),
    ]))
    story.append(total_table)

    # --- 6. Conclusão e Envio ---
    doc.build(story)
    pdf = buffer.getvalue()
    buffer.close()

    return Response(
        pdf,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": "inline; filename=cobranca_debitos.pdf"
        }
    )

# ----------------------------------------------------
# ROTA 10.9 - Exportar Relatório Gerencial em PDF
# ----------------------------------------------------
@app.route("/exportar_relatorio_pdf", methods=["POST"])
@login_required
def exportar_relatorio_pdf():
    from datetime import datetime
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors

    # --- 1. Captura dos filtros do formulário ---
    data_inicio = request.form.get("data_inicio")
    data_fim = request.form.get("data_fim")
    cliente_id = request.form.get("cliente_id")
    tipo_servico = request.form.get("tipo_servico")

    def parse_date(date_str):
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else None
        except Exception:
            return None

    data_inicio = parse_date(data_inicio)
    data_fim = parse_date(data_fim)

    # --- 2. Consultas com filtros ---
    query_servicos = Servico.query
    query_mov = MovimentacaoCaixa.query
    query_despesas = Despesa.query

    if data_inicio:
        query_servicos = query_servicos.filter(Servico.data_servico >= data_inicio)
        query_mov = query_mov.filter(MovimentacaoCaixa.data >= data_inicio)
        query_despesas = query_despesas.filter(Despesa.data >= data_inicio)

    if data_fim:
        query_servicos = query_servicos.filter(Servico.data_servico <= data_fim)
        query_mov = query_mov.filter(MovimentacaoCaixa.data <= data_fim)
        query_despesas = query_despesas.filter(Despesa.data <= data_fim)

    if cliente_id:
        query_servicos = query_servicos.filter(Servico.cliente_id == cliente_id)

    if tipo_servico:
        query_servicos = query_servicos.filter(Servico.tipo_servico == tipo_servico)

    servicos = query_servicos.all()
    movimentacoes_brutas = query_mov.all() # Consulta inicial
    
    # --- 2.1 FILTRAGEM DE MOVIMENTAÇÕES ÓRFÃS (CORREÇÃO ESSENCIAL) ---
    movimentacoes = []
    for m in movimentacoes_brutas:
        if m.referencia_tipo == 'Servico':
            servico_existente = Servico.query.get(m.referencia_id)
            if servico_existente:
                movimentacoes.append(m)
        else:
            movimentacoes.append(m) 
    # -------------------------------------------------------------

    despesas = query_despesas.all()

    # --- 3. Cálculos (CORRIGIDOS) ---
    despesas = query_despesas.all() # Executa a consulta de despesas

    total_faturado = sum(s.valor_total for s in servicos)
    total_recebido = sum(s.valor_recebido for s in servicos)
    
    # 🚀 CORREÇÃO 1: Incluir movimentos que são do tipo 'entrada' OU que são referenciados a um Serviço (Pagamentos parciais/totais).
    total_entradas = sum(m.valor for m in movimentacoes if m.tipo and (
        'entrada' in m.tipo.lower() or m.referencia_tipo == 'Servico'
    ))

    # Total de Saídas da tabela MovimentacaoCaixa (inclui Saídas diversas, se houver)
    total_saidas_caixa = sum(m.valor for m in movimentacoes if m.tipo and 'saida' in m.tipo.lower())
    
    # ✅ 1. Calcular o total de Despesas da tabela Despesa
    total_despesas_avulsas = sum(d.valor for d in despesas)
    
    # ✅ 2. O Total de Saídas GERAL deve ser a soma das saídas do caixa + despesas (para o cálculo do Saldo)
    total_saidas_geral = total_saidas_caixa + total_despesas_avulsas

    # ✅ 3. O saldo líquido agora subtrai o TOTAL de saídas
    saldo_liquido = total_entradas - total_saidas_geral 

    # --- 4. Criação do PDF ---
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=1*cm, bottomMargin=1*cm)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("<b>Relatório Gerencial - Despachante Machado</b>", styles["Title"]))
    story.append(Spacer(1, 12))

    periodo = f"{data_inicio.strftime('%d/%m/%Y') if data_inicio else 'Início'} a {data_fim.strftime('%d/%m/%Y') if data_fim else 'Hoje'}"
    story.append(Paragraph(f"Período: {periodo}", styles["Normal"]))
    story.append(Spacer(1, 12))

    # Resumo
    resumo_data = [
        ["Total Faturado", f"R$ {total_faturado:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")],
        ["Total Recebido", f"R$ {total_recebido:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")],
        ["Total Entradas", f"R$ {total_entradas:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")],
        # ⚠️ ATUALIZAÇÃO AQUI para mostrar o TOTAL de SAÍDAS (Caixa + Despesas)
        ["Total Saídas (Geral)", f"R$ {total_saidas_geral:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")], 
        ["Saldo Líquido", f"R$ {saldo_liquido:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")],
    ]
    resumo_table = Table(resumo_data, hAlign="LEFT")
    resumo_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
    ]))
    story.append(resumo_table)
    story.append(Spacer(1, 20))

    # --- 5. Tabela de Serviços ---
    if servicos:
        story.append(Paragraph("<b>Serviços Prestados</b>", styles["Heading2"]))
        tabela_servicos = [["Data", "Cliente", "Tipo", "Valor Total", "Recebido", "Pendente"]]
        for s in servicos:
            tabela_servicos.append([
                s.data_servico.strftime("%d/%m/%Y") if s.data_servico else "-",
                s.cliente.nome,
                s.tipo_servico,
                f"R$ {s.valor_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                f"R$ {s.valor_recebido:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                f"R$ {(s.valor_total - s.valor_recebido):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            ])
        t = Table(tabela_servicos, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ]))
        story.append(t)
        story.append(Spacer(1, 20))

    # --- 6. Monta o PDF ---
    doc.build(story)
    pdf = buffer.getvalue()
    buffer.close()

    return Response(
        pdf,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": "inline; filename=relatorio_gerencial.pdf"
        }
    )


# -----------------------------------------------
# 11. ROTAS DE COLABORADORES/ADMIN (Nenhuma alteração aqui)
# ----------------------------------------------------

# Cadastro e Edição de Colaborador (ADMIN)
@app.route('/colaborador/cadastro', methods=['GET', 'POST'])
@app.route('/colaborador/cadastro/<int:usuario_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def colaborador_cadastro(usuario_id=None):
    """
    Rota unificada para cadastro e edição de colaboradores.
    Se 'usuario_id' for fornecido, realiza edição; caso contrário, cadastro novo.
    Apenas ADMIN tem acesso.
    """
    usuario = None
    if usuario_id:
        usuario = Usuario.query.get_or_404(usuario_id)

    if request.method == 'POST':
        nome = request.form.get('nome')
        login_user = request.form.get('login')
        senha = request.form.get('senha')
        nivel_acesso = request.form.get('nivel_acesso', 'COLABORADOR').upper()

        # Validação básica de campos obrigatórios
        if not nome or not login_user:
            flash('Preencha todos os campos obrigatórios.', 'error')
            return redirect(request.url)

        # Verifica se o login já existe para outro usuário
        login_existente = Usuario.query.filter_by(login=login_user).first()
        if login_existente and (not usuario or login_existente.id != usuario.id):
            flash('Login já existe para outro colaborador.', 'error')
            return redirect(request.url)

        if usuario:
            # Edição de usuário existente
            usuario.nome = nome
            usuario.login = login_user
            usuario.nivel_acesso = nivel_acesso
            if senha:
                usuario.set_senha(senha)  # Atualiza senha somente se informada
            try:
                db.session.commit()
                flash(f'Colaborador "{usuario.nome}" atualizado com sucesso!', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Erro ao atualizar colaborador: {e}', 'error')
        else:
            # Novo cadastro
            try:
                novo_usuario = Usuario(
                    nome=nome,
                    login=login_user,
                    nivel_acesso=nivel_acesso
                )
                novo_usuario.set_senha(senha)
                db.session.add(novo_usuario)
                db.session.commit()
                flash(f'Colaborador "{nome}" cadastrado com sucesso!', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Erro ao cadastrar colaborador: {e}', 'error')
                return redirect(request.url)

        return redirect(url_for('colaborador_lista'))

    # Renderiza o template de cadastro/edição, passando 'usuario' para preencher os campos
    return render_template('colaborador_cadastro.html', usuario=usuario)


# Lista de todos os colaboradores (ADMIN)
@app.route('/colaboradores', methods=['GET'])
@login_required
@admin_required
def colaborador_lista():
    """
    Exibe todos os colaboradores cadastrados.
    Apenas ADMIN pode acessar.
    """
    try:
        usuarios = Usuario.query.order_by(Usuario.id.desc()).all()  # Lista do mais recente para o mais antigo
    except Exception as e:
        flash(f'Erro ao carregar lista de colaboradores: {e}', 'error')
        usuarios = []

    return render_template('colaborador_lista.html', usuarios=usuarios)

# ----------------------------------------------------
# 12. INICIALIZAÇÃO E TESTE
# ----------------------------------------------------

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        
        # Cria um usuário ADMIN se não existir
        if not Usuario.query.filter_by(login='admin').first():
            admin_user = Usuario(nome='Administrador', login='admin', nivel_acesso='ADMIN')
            admin_user.set_senha('123456') # Mude para uma senha forte!
            db.session.add(admin_user)
            db.session.commit()
            print("Usuário ADMIN criado (login: admin, senha: 123456)")
            
    app.run(debug=True)