import os
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from pipeline.llm_transport import llm_default_headers

REPO_PATH = "/"
JAVA_SRC_PATH = REPO_PATH + "/src/main/java"
SMELL_DOC_PATH = REPO_PATH + "/docs/smells"
RULE_DOC_PATH = REPO_PATH + "/docs/rules"
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 200
# EMBEDDINGS_MODEL = HuggingFaceEmbeddings()

RETRIEVER_SEARCH_TYPE = "mmr"
RETRIEVER_K = 8

documents_cache = {}
texts_cache = {}
db_cache = None

llm_model = None
llm_model_config = None


def get_llm_model(session_id=None):
    # Resolve each time: local edits take effect without a stale global client.
    from pipeline.model_config import chat_client
    return chat_client('refactoring', session_id=session_id, factory=ChatOpenAI)


system_role = """
You are a java testing expert, and now you need to refactor the test code which contains bad test code smells.
"""


def get_default_output_dir():
    output_root = os.getenv("UTREFACTOR_OUTPUT_DIR", "data")
    experiment_name = os.getenv("UTREFACTOR_EXPERIMENT", _resolve_run_id(output_root))
    project_name = os.getenv("UTREFACTOR_PROJECT_NAME", "")
    output_dir = os.path.join(output_root, experiment_name, project_name, "refactoring") if project_name else os.path.join(output_root, experiment_name, "refactoring")
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def _resolve_run_id(data_root):
    run_id = os.getenv("UTREFACTOR_RUN_ID", "").strip()
    if run_id:
        return run_id
    latest_file = os.path.join(data_root, "latest")
    if os.path.isfile(latest_file):
        with open(latest_file, "r", encoding="utf-8") as f:
            run_id = f.read().strip()
        if run_id:
            return run_id
    raise FileNotFoundError(
        "Could not resolve run_id. Set UTREFACTOR_RUN_ID env var or run the Java "
        "main pipeline first (it writes data/latest)."
    )


#context 实际上主要是模块/类路径信息，不是测试代码上下文内容。
def build_prompt_total(test_code, context, smell_list, smell_desc_list, refactoring_dsl,
                       smell_refactoring_checkpoint_list, retrieved_cases="",
                       target_method_code="", dependency_context="", validation_feedback=""):
    validation_feedback_section = ""
    if validation_feedback:
        validation_feedback_section = f"""

    Previous output was rejected by an automatic Java syntax/format validator.
    You must correct the following issues in the new output:
    {validation_feedback}
    """
    prompt_template = """
    {system_role}

    Your task is to refactor the given Java test code to eliminate the listed test smells while preserving the original test intent and behavior.

    Priority rules:
    1. Preserve the original assertions and tested behavior.
    2. Do not modify production code.
    3. Treat the target method source code as the primary semantic reference when it is provided.
    4. Use dependency context only to understand collaborators, fixtures, mocks, constructors, side effects, constants, and helper calls.
    5. Use retrieved RAG refactoring cases as reference examples, not as code to copy directly.
    6. Apply the provided refactoring DSL and satisfy every checkpoint.
    7. If you need to add comments in the refactored Java code, use // comment format only.
    8. Output exactly one ```java code block and no explanations outside the code block.
    9. The code block must contain exactly one complete Java test method: the rewritten target method.
    10. Refactor exactly the provided test method. Keep the same method name and method signature.
    11. Preserve the original test framework, assertion style, and annotation style unless the DSL explicitly requires a change.
    12. Do not convert a normal @Test method into @ParameterizedTest, @RepeatedTest, @TestFactory, or @TestTemplate unless the DSL explicitly requires it.
    13. Do not split one input test method into multiple @Test methods.
    14. Do not introduce any annotation, assertion API, testing API, type, static method, helper method, field, or import that is not already available in the original test class.
    15. Do not output package declarations, import statements, class declarations, interface declarations, enum declarations, or record declarations.
    16. Avoid large-scale rewriting. Keep the refactored code structurally close to the original method and prefer minimal local refactoring.
    17. Every method, lambda, anonymous class, and block you open must be fully closed in the same fragment.
    18. Do not declare helper methods or fields. If a rule would require a helper, leave that smell unchanged for manual review.
    19. The fragment itself must have balanced braces, parentheses, brackets, string literals, char literals, and comments.

    [Test Code]
    {test_code}

    [Original Test Context]   
    {context}

    [Target Method Source Code]
    {target_method_code}

    [Dependency Context]
    {dependency_context}

    [Retrieved RAG Refactoring Cases]
    {retrieved_cases}

    [Test Smells]
    {smell_list}

    [Smell Explanations]
    {smell_desc_list}

    [Refactoring DSL]
    {refactoring_dsl}

    [Refactoring Checkpoints]
    {smell_refactoring_checkpoint_list}

    Internal verification before final output:
    1. Every listed smell has been addressed.
    2. The refactored test still targets the same behavior.
    3. Assertions remain semantically equivalent.
    4. Mocks, fixtures, helper methods, and dependencies remain valid.
    5. No unrelated behavior has been introduced.
    6. The returned fragment is syntactically valid inside an existing Java class body and leaves no block unclosed.
    {validation_feedback_section}
    """
    template = PromptTemplate(
        input_variables=["system_role", "test_code", "context", "target_method_code", "dependency_context",
                         "retrieved_cases", "smell_list", "smell_desc_list", "refactoring_dsl",
                         "smell_refactoring_checkpoint_list", "validation_feedback_section"],
        template=prompt_template
    )

    final_prompt = template.format(
        system_role=system_role,
        test_code=test_code,
        context=context,
        target_method_code=target_method_code,
        dependency_context=dependency_context,
        retrieved_cases=retrieved_cases,
        smell_list=smell_list,
        smell_desc_list=smell_desc_list,
        refactoring_dsl=refactoring_dsl,
        smell_refactoring_checkpoint_list=smell_refactoring_checkpoint_list,
        validation_feedback_section=validation_feedback_section
    )

    return final_prompt


def build_prompt_1(source_code, context):
    prompt_template = """
    {system_role}
    Given the test code and its context, please provide a brief summary of the design intent of the test. 
    Only provide a concise summary, without any additional modifications or suggestions.
    Test Code: 
    {source_code}
    Context of the Test Code: 
    {context}
    Summary:
    """
    template = PromptTemplate(
        input_variables=["system_role", "source_code", "context"],
        template=prompt_template
    )

    final_prompt = template.format(
        system_role=system_role,
        source_code=source_code,
        context=context
    )

    return final_prompt


def build_prompt_2(test_code, intent, smell_list, smell_desc_list):
    prompt_template = """
    Please provide a brief insight of the existing code smells.
    Only provide a concise summary, without any additional modifications or suggestions.
    The test code: 
    {test_code} 
    The design intent is: 
    {intent}
    The test has the following code smells:
    {smell_list}
    The explanation for each smell is:
    {smell_desc_list}
    """
    template = PromptTemplate(
        input_variables=["test_code", "intent", "smell_list", "smell_desc_list"],
        template=prompt_template
    )

    final_prompt = template.format(
        test_code=test_code,
        intent=intent,
        smell_list=smell_list,
        smell_desc_list=smell_desc_list,
    )

    return final_prompt


def build_prompt_3(test_code, smell, refactoring_dsl):
    prompt_template = """
    Please refer to the test code and the corresponding refactoring approach to provide an analysis and conclusion on how the test code should be refactored.
    Only provide a concise summary, without any additional modifications or suggestions, or code examples.
    For the test code and the existing smell,
    {test_code}
    {smell}
    The corresponding refactoring DSL to eliminate the bad smell is:
    {refactoring_dsl}
    """
    template = PromptTemplate(
        input_variables=["test_code", "smell", "refactoring_dsl"],
        template=prompt_template
    )

    final_prompt = template.format(
        test_code=test_code,
        smell=smell,
        refactoring_dsl=refactoring_dsl
    )

    return final_prompt


def build_prompt_4(test_code, refactoring_steps):
    prompt_template = """
    For the above analysis, refactor the test and provide the refactored test code.
    Return exactly one ```java code block and nothing else. The code block must contain exactly one complete rewritten test method.
    Refactor exactly the provided test method and keep the same method name/signature.
    Preserve the original test framework, assertion style, and annotation style unless the refactoring rule explicitly requires a change.
    Do not convert a normal @Test method into @ParameterizedTest, @RepeatedTest, @TestFactory, or @TestTemplate unless explicitly required by the rule.
    Do not split one input test method into multiple @Test methods.
    Do not introduce any annotation, assertion API, testing API, type, static method, helper method, field, or import that is not already available in the original test class.
    Do not declare helper methods or fields.
    Avoid large-scale rewriting. Keep the refactored code structurally close to the original method and prefer minimal local refactoring.
    Do not output package declarations, import statements, class declarations, interface declarations, enum declarations, or record declarations.
    The fragment must be complete and balanced: every method, lambda, anonymous class, brace, parenthesis, bracket, string literal, char literal, and comment must be closed.
    {test_code} 
    {refactoring_steps}
    """
    template = PromptTemplate(
        input_variables=["test_code", "refactoring_steps"],
        template=prompt_template
    )

    final_prompt = template.format(
        test_code=test_code,
        refactoring_steps=refactoring_steps
    )

    return final_prompt


def load_documents(repo_path, glob_pattern, doc_type):
    from langchain_community.document_loaders import DirectoryLoader, TextLoader

    if doc_type not in documents_cache:
        loader = DirectoryLoader(
            repo_path,
            glob=glob_pattern,
            loader_cls=TextLoader,
            use_multithreading=True
        )
        documents_cache[doc_type] = loader.load()
    return documents_cache[doc_type]


def split_documents(documents, language):
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    if language not in texts_cache:
        splitter = RecursiveCharacterTextSplitter.from_language(
            language=language, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
        )
        texts_cache[language] = splitter.split_documents(documents)
    return texts_cache[language]


# def create_db(texts):
#     global db_cache
#     if db_cache is None:
#         db_cache = Chroma.from_documents(texts, EMBEDDINGS_MODEL)
#     return db_cache


def create_prompt_templates():
    from langchain_core.prompts import ChatPromptTemplate

    search_prompt = ChatPromptTemplate.from_messages(
        [
            ("placeholder", "{chat_history}"),
            ("user", "{input}"),
            (
                "user",
                "Given the above conversation, generate a search query to look up to get information relevant to the conversation",
            ),
        ]
    )

    answer_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "Answer the user's questions based on the below context:\\\\n\\\\n{context}",
            ),
            ("placeholder", "{chat_history}"),
            ("user", "{input}"),
        ]
    )
    return search_prompt, answer_prompt


def create_qa_chain(llm, db, search_prompt, answer_prompt):
    from langchain.chains.combine_documents import create_stuff_documents_chain
    from langchain.chains.history_aware_retriever import create_history_aware_retriever
    from langchain.chains.retrieval import create_retrieval_chain

    retriever = db.as_retriever(
        search_type=RETRIEVER_SEARCH_TYPE,
        search_kwargs={"k": RETRIEVER_K},
    )
    retriever_chain = create_history_aware_retriever(llm, retriever, search_prompt)
    document_chain = create_stuff_documents_chain(llm, answer_prompt)
    return create_retrieval_chain(retriever_chain, document_chain)


def get_specific_test_documents(test_code_path, smell_type, rule_type):
    from langchain_text_splitters import Language

    test_code_documents = load_documents(test_code_path, "*.java", "test_code")
    test_code_texts = split_documents(test_code_documents, Language.JAVA)

    smell_documents = load_documents(SMELL_DOC_PATH, f"*{smell_type}*.txt", "smells")
    smell_texts = split_documents(smell_documents, Language.JAVA)

    rule_documents = load_documents(RULE_DOC_PATH, f"*{rule_type}*.txt", "rules")
    rule_texts = split_documents(rule_documents, Language.JAVA)

    return test_code_texts + smell_texts + rule_texts


def refactoring_test_qa(task_idx, llm, test_code, context, smell_list, smell_desc_list, refactoring_dsl,
                        smell_refactoring_checkpoint_list, retrieved_cases="", target_method_code="",
                        dependency_context="", output_dir=None, log_prefix="", validation_feedback=""):
    question = build_prompt_total(test_code, context, smell_list, smell_desc_list, refactoring_dsl,
                                  smell_refactoring_checkpoint_list, retrieved_cases, target_method_code,
                                  dependency_context, validation_feedback)

    output_dir = output_dir or get_default_output_dir()
    os.makedirs(output_dir, exist_ok=True)
    prompt_log_name = "refactoring_log_prompt_" + (log_prefix + "_" if log_prefix else "") + str(task_idx) + ".txt"
    file_name = os.path.join(output_dir, prompt_log_name)
    with open(file_name, 'w', encoding='utf-8') as file:
        file.write("Prompt:\n")
        file.write(question)
        file.write("\n")

    result = llm.invoke(question)
    return result


def step1_understand_test_context(llm, test_code, context):
    question = build_prompt_1(test_code, context)
    result = llm.invoke(question)
    return result


def step2_understand_test_code_smell(llm, test_code, intent, smell_list, smell_desc_list):
    question = build_prompt_2(test_code, intent, smell_list, smell_desc_list)
    result = llm.invoke(question)
    return result


def step3_understand_test_code_refactoring_rules(llm, test_code, smell, refactoring_dsl):
    question = build_prompt_3(test_code, smell, refactoring_dsl)
    result = llm.invoke(question)
    return result


def step4_refactoring_test_code(llm, test_code, refactoring_steps):
    question = build_prompt_4(test_code, refactoring_steps)
    result = llm.invoke(question)
    return result


# To create a Python function that writes the input parameters and intermediate steps to a text file, we can use the following code:


def refactoring_test_one_qa(filename, task_idx, test_code, context, smell_list, smell_desc_list, refactoring_dsl,
                            smell_refactoring_checkpoint_list, retrieved_cases="", target_method_code="",
                            dependency_context="", output_dir=None, validation_feedback=""):
    output_dir = output_dir or get_default_output_dir()
    os.makedirs(output_dir, exist_ok=True)
    file_name = os.path.join(output_dir, "refactoring_log_" + filename + "_" + str(task_idx) + ".txt")

    # Open the file in write mode
    with open(file_name, 'w', encoding='utf-8') as file:
        # Write the input parameters to the file
        file.write("Input Parameters:\n")
        file.write(f"Test Code: {test_code}\n")
        file.write(f"Context: {context}\n")
        file.write(f"Target Method Code: {target_method_code}\n")
        file.write(f"Dependency Context: {dependency_context}\n")
        file.write(f"Retrieved Cases: {retrieved_cases}\n")
        file.write(f"Smell List: {smell_list}\n")
        file.write(f"Smell Description List: {smell_desc_list}\n")
        file.write(f"Refactoring DSL: {refactoring_dsl}\n")
        file.write(f"Checkpoints: {smell_refactoring_checkpoint_list}\n")
        if validation_feedback:
            file.write(f"Validation Feedback: {validation_feedback}\n")
        file.write("\n")

        refactored_code = refactoring_test_qa(task_idx, get_llm_model(), test_code, context, smell_list, smell_desc_list,
                                              refactoring_dsl, smell_refactoring_checkpoint_list, retrieved_cases,
                                              target_method_code, dependency_context, output_dir, filename,
                                              validation_feedback)
        file.write(f"Refactored Code: {refactored_code}\n")
        file.write("\n")

    # Return the refactored code
    return refactored_code


def refactoring_test_multi_qa(task_idx, test_code, context, smell_list, smell_desc_list, refactoring_dsl,
                              output_dir=None):
    output_dir = output_dir or get_default_output_dir()
    os.makedirs(output_dir, exist_ok=True)
    file_name = os.path.join(output_dir, "refactoring_log_" + str(task_idx) + ".txt")

    # Open the file in write mode
    with open(file_name, 'w', encoding='utf-8') as file:
        # Write the input parameters to the file
        file.write("Input Parameters:\n")
        file.write(f"Test Code: {test_code}\n")
        file.write(f"Context: {context}\n")
        file.write(f"Target Method Code: {target_method_code}\n")
        file.write(f"Dependency Context: {dependency_context}\n")
        file.write(f"Retrieved Cases: {retrieved_cases}\n")
        file.write(f"Smell List: {smell_list}\n")
        file.write(f"Smell Description List: {smell_desc_list}\n")
        file.write(f"Refactoring DSL: {refactoring_dsl}\n")
        file.write("\n")

        # Step 1: Understand test context
        file.write("Step 1: Understand Test Context\n")
        step1_result = step1_understand_test_context(llm_model, test_code, context)
        file.write(f"Step 1 Result: {step1_result}\n")
        file.write("\n")

        # Step 2: Understand test code smell
        file.write("Step 2: Understand Test Code Smell\n")
        step2_result = step2_understand_test_code_smell(llm_model, test_code, step1_result, smell_list, smell_desc_list)
        file.write(f"Step 2 Result: {step2_result}\n")
        file.write("\n")

        # Step 3: Understand test code refactoring rules
        file.write("Step 3: Understand Test Code Refactoring Rules\n")
        step3_result = step3_understand_test_code_refactoring_rules(llm_model, test_code, step2_result, refactoring_dsl)
        file.write(f"Step 3 Result: {step3_result}\n")
        file.write("\n")

        # Step 4: Refactoring test code
        file.write("Step 4: Refactoring Test Code\n")
        refactored_code = step4_refactoring_test_code(llm_model, test_code, step3_result)
        file.write(f"Refactored Code: {refactored_code}\n")
        file.write("\n")

    # Return the refactored code
    return refactored_code








