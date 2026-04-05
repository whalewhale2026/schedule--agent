import streamlit as st
import pandas as pd
import networkx as nx
import re
import json
from openai import OpenAI

# ==========================================
# 1. 页面配置与 UI 初始化
# ==========================================
st.set_page_config(page_title="进度智能优化专家", page_icon="🏗️", layout="wide")

st.title("🏗️ 施工组织设计：进度智能优化专家")
st.markdown("上传斑马进度计划 (Excel/CSV)，AI 自动计算关键路径、诊断逻辑死角，并生成【工期-成本】最优赶工方案。")

# 侧边栏：配置区
with st.sidebar:
    st.header("⚙️ 系统配置")
    # 让用户可以输入自己的 API Key，默认填入你的
    api_key = st.text_input("DeepSeek API Key", value="sk-2786863281994bc5ba7f21e4b82752f5", type="password")
    st.markdown("---")
    st.markdown("💡 **使用说明**\n1. 导出斑马进度计划为 Excel/CSV。\n2. 确保包含 TaskID, TaskName, Duration, Dependencies 字段。\n3. 点击上传即可自动分析。")

# 初始化 AI 客户端
client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

# ==========================================
# 2. 核心算法函数 (适配 Streamlit 返回值)
# ==========================================
def clean_id(x):
    if pd.isna(x): return ""
    try: return str(int(float(x)))
    except: return str(x).strip()

def read_zebra_schedule(uploaded_file):
    df = pd.read_excel(uploaded_file) if uploaded_file.name.endswith(('.xlsx', '.xls')) else pd.read_csv(uploaded_file)
    tasks = {}
    for _, row in df.iterrows():
        task_id = clean_id(row.get('TaskID', ''))
        if not task_id: continue
        task_name = str(row.get('TaskName', '')).strip()
        if task_name.startswith('*'): continue 
        duration = float(row.get('Duration', 0)) if pd.notna(row.get('Duration')) else 0
        deps_str = str(row.get('Dependencies', ''))
        deps = re.findall(r'\d+', deps_str) if deps_str.lower() != 'nan' else []
        tasks[task_id] = {'id': task_id, 'name': task_name, 'duration': duration, 'dependencies': deps}
    return tasks

def diagnose_schedule(tasks):
    G = nx.DiGraph()
    for task_id, task in tasks.items():
        G.add_node(task_id, duration=task['duration'], name=task['name'])
    for task_id, task in tasks.items():
        for dep in task['dependencies']:
            if dep in tasks: G.add_edge(dep, task_id)
            
    # 拓扑检测
    try:
        cycles = list(nx.find_cycle(G))
        return False, f"❌ 致命错误：检测到进度逻辑存在循环依赖（死锁）。循环节点：{cycles}", None, None
    except nx.NetworkXNoCycle:
        pass
        
    # AI 诊断
    tasks_summary = [{"id": v['id'], "name": v['name'], "duration": v['duration'], "deps": v['dependencies']} for k, v in tasks.items()]
    prompt = f"""
    审查施工进度计划，找出【不科学】或【不合理】的安排。
    任务数据：{json.dumps(tasks_summary[:100], ensure_ascii=False)} 
    如果没有明显不合理，返回空数组 []。
    如果有，严格输出 JSON 数组：[{{"task_id": "ID", "issue": "问题", "suggestion": "建议"}}]
    """
    try:
        response = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
        json_str = re.search(r'\[.*\]', response.choices[0].message.content, re.DOTALL)
        issues = json.loads(json_str.group()) if json_str else []
        return True, "✅ 拓扑图构建成功", G, issues
    except Exception as e:
        return True, "⚠️ 拓扑图构建成功，但 AI 诊断模块连接失败。", G, []

def calculate_cpm(G):
    for node in nx.topological_sort(G):
        es = max([G.nodes[pred].get('ef', 0) for pred in G.predecessors(node)] + [0])
        G.nodes[node]['es'] = es
        G.nodes[node]['ef'] = es + G.nodes[node]['duration']
    total_duration = max([G.nodes[node]['ef'] for node in G.nodes])
    for node in reversed(list(nx.topological_sort(G))):
        lf = min([G.nodes[succ].get('ls', total_duration) for succ in G.successors(node)] + [total_duration])
        G.nodes[node]['lf'] = lf
        G.nodes[node]['ls'] = lf - G.nodes[node]['duration']
        G.nodes[node]['tf'] = G.nodes[node]['ls'] - G.nodes[node]['es']
    critical_path = [n for n in G.nodes if G.nodes[n]['tf'] == 0]
    return total_duration, critical_path

def get_optimization_plan(G, total_duration, critical_path):
    cp_tasks_info = [{"id": n, "name": G.nodes[n]['name'], "duration": G.nodes[n]['duration']} for n in critical_path]
    prompt = f"""
    项目总工期 {total_duration} 天。关键路径任务：{json.dumps(cp_tasks_info, ensure_ascii=False)}
    选择 2-3 个最具压缩潜力的任务（最多压缩原工期25%），并估算成本。
    严格输出 JSON 数组：
    [{{ "id": "ID", "reduction": 压缩天数, "extra_labor_per_day": 新增劳务人数, "extra_cost_total": 新增总成本, "reason": "具体措施" }}]
    """
    response = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
    json_str = re.search(r'\[.*\]', response.choices[0].message.content, re.DOTALL)
    return json.loads(json_str.group()) if json_str else []

# ==========================================
# 3. 网页主交互逻辑
# ==========================================
uploaded_file = st.file_uploader("📂 请选择您的斑马进度计划文件 (Excel/CSV)", type=['xlsx', 'xls', 'csv'])

if uploaded_file is not None:
    if st.button("🚀 开始智能分析与优化", type="primary"):
        
        # 使用选项卡组织内容
        tab1, tab2, tab3 = st.tabs(["🩺 进度诊断报告", "📊 初始排程分析", "💎 施组优化决策"])
        
        with st.spinner('🔄 正在读取并清洗数据...'):
            tasks = read_zebra_schedule(uploaded_file)
            
        with tab1:
            st.subheader("一、 进度计划科学性审查")
            with st.spinner('🤖 AI 正在进行工程常识诊断...'):
                is_valid, msg, G_initial, issues = diagnose_schedule(tasks)
            
            if not is_valid:
                st.error(msg)
                st.stop() # 停止运行后续代码
                
            if issues:
                st.warning("⚠️ 发现以下不合理安排，建议在施组中进行防范：")
                for issue in issues:
                    t_name = tasks.get(str(issue['task_id']), {}).get('name', '未知任务')
                    st.error(f"**[{t_name}]** 问题: {issue['issue']} 👉 **建议:** {issue['suggestion']}")
            else:
                st.success("✅ AI 审查通过，未发现明显的工程逻辑异常。")

        with tab2:
            st.subheader("二、 关键路径 (CPM) 计算结果")
            total_dur_initial, cp_initial = calculate_cpm(G_initial)
            
            col1, col2 = st.columns(2)
            col1.metric("初始总工期 (天)", f"{total_dur_initial}")
            col2.metric("关键节点数量", f"{len(cp_initial)}")
            
            st.write("📍 **关键路径任务清单：**")
            cp_names = [G_initial.nodes[n]['name'] for n in cp_initial]
            st.info(" ➡️ ".join(cp_names))

        with tab3:
            st.subheader("三、 技术经济优化与决策面板")
            with st.spinner('🧠 正在模拟多种赶工方案并测算成本...'):
                try:
                    suggestions = get_optimization_plan(G_initial, total_dur_initial, cp_initial)
                except Exception as e:
                    st.error("AI 方案生成失败，请重试。")
                    st.stop()
            
            if suggestions:
                optimized_G = G_initial.copy()
                table_data = []
                total_extra_cost = 0
                total_max_labor = 0
                
                for sug in suggestions:
                    t_id = str(sug['id'])
                    if t_id in optimized_G:
                        t_name = optimized_G.nodes[t_id]['name']
                        old_dur = optimized_G.nodes[t_id]['duration']
                        optimized_G.nodes[t_id]['duration'] = max(1, old_dur - sug['reduction'])
                        
                        total_extra_cost += sug['extra_cost_total']
                        total_max_labor = max(total_max_labor, sug['extra_labor_per_day'])
                        
                        table_data.append({
                            "任务名称": t_name,
                            "原工期(天)": old_dur,
                            "拟压缩(天)": sug['reduction'],
                            "新增劳务(人/天)": sug['extra_labor_per_day'],
                            "预计赶工费(元)": f"¥ {sug['extra_cost_total']:,}",
                            "技术组织措施": sug['reason']
                        })
                
                # 渲染漂亮的 DataFrame 表格
                st.dataframe(pd.DataFrame(table_data), use_container_width=True)
                
                # 重新计算
                new_total_duration, _ = calculate_cpm(optimized_G)
                actual_days_saved = total_dur_initial - new_total_duration
                
                st.divider()
                st.write("### 📈 综合效益评估")
                if actual_days_saved > 0:
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("优化后总工期", f"{new_total_duration} 天", f"-{actual_days_saved} 天")
                    c2.metric("总赶工投入", f"¥ {total_extra_cost:,}")
                    c3.metric("劳务峰值增量", f"{total_max_labor} 人")
                    c4.metric("单日赶工斜率", f"¥ {total_extra_cost/actual_days_saved:,.0f} / 天")
                    
                    st.success(f"**📝 施组编写参考语段：**\n经初始进度排查与拓扑演算，拟对关键工序实施重点保障。通过采取增加作业面等措施，重点对【{', '.join([d['任务名称'] for d in table_data])}】进行工期压缩。经测算，该方案预计投入赶工专项费用 {total_extra_cost:,} 元，施工高峰期需增派劳务人员约 {total_max_labor} 人，可成功使总工期提前 {actual_days_saved} 天，具备良好的技术经济效益。")
                else:
                    st.error("⚠️ 警告：关键路径发生转移，当前压缩方案未能有效缩短总工期，建议更换优化节点！")