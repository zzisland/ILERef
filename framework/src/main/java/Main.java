import com.github.javaparser.utils.Log;
import entity.RefactoringLLMContext;
import entity.RunContext;
import entity.TaskParam;
import entity.TestSmellDetectionOutput;
import service.TaskParamReader;
import service.TestContextCollection;
import service.detection.TestSmellDetection;
import service.preprocess.FocalMethodExtractor;
import service.preprocess.ProjectRunCleaner;
import service.refactor.TestSmellRefactorRuleSetting;
import util.FileUtil;

import java.util.List;

public class Main {
    public static void main(String[] args) {
        TaskParam taskParam = TaskParamReader.getTaskParam();
        String projectName = resolveProjectName(taskParam.getProjectFilepath());
        boolean detectionOnly = isDetectionOnly();

        RunContext runContext = RunContext.create(projectName);
        if (detectionOnly) {
            runContext.ensureDetectionDirectories();
        } else {
            runContext.ensureDirectories();
        }
        Log.info("RunContext created: " + runContext.getRunDir());

        new ProjectRunCleaner().clean(taskParam.getProjectFilepath());
        Log.info("ProjectRunCleaner done");

        long startTime1 = System.currentTimeMillis();
        TestSmellDetection testSmellDetection = new TestSmellDetection();
        List<TestSmellDetectionOutput> testSmellDetectionOutputs = testSmellDetection.smellDetect(projectName, runContext);
        long endTime1 = System.currentTimeMillis();
        Log.info("TestSmellDetection, time: " + (endTime1 - startTime1));

        if (detectionOnly) {
            Log.info("Detection-only mode: skipping focal-method extraction, JSON generation, RAG, and refactoring");
            Log.info("Output dir: " + runContext.getRunDir());
            Log.info("Main, time: " + (endTime1 - startTime1));
            return;
        }

        TestSmellRefactorRuleSetting refactorRuleSetting = new TestSmellRefactorRuleSetting();
        List<TestSmellDetectionOutput> testSmellList = refactorRuleSetting.selectSmells(testSmellDetectionOutputs);

        long startTime3 = System.currentTimeMillis();
        FocalMethodExtractor focalMethodExtractor = new FocalMethodExtractor();
        focalMethodExtractor.fillMethodSignature(testSmellList);
        long endTime3 = System.currentTimeMillis();
        Log.info("FocalMethodExtractor, time: " + (endTime3 - startTime3));

        long startTime4 = System.currentTimeMillis();
        TestContextCollection testContextCollection = new TestContextCollection();
        List<RefactoringLLMContext> refactoringLLMContexts = testContextCollection.buildRefactoringLLMContextList(testSmellList);
        testContextCollection.collectTestToJson(projectName, refactoringLLMContexts, runContext);
        long endTime4 = System.currentTimeMillis();
        Log.info("TestContextCollection, time: " + (endTime4 - startTime4));

        runContext.writeLatest();
        Log.info("Output dir: " + runContext.getRunDir());
        Log.info("Main, time: " + (endTime4 - startTime1));
    }

    private static String resolveProjectName(String projectFilepath) {
        String projectName = System.getProperty("utrefactor.projectName");
        if (projectName == null || projectName.isBlank()) {
            projectName = System.getenv("UTREFACTOR_PROJECT_NAME");
        }
        if (projectName == null || projectName.isBlank()) {
            projectName = FileUtil.getNameByFilepath(projectFilepath);
        }
        return projectName;
    }

    private static boolean isDetectionOnly() {
        String mode = System.getProperty("utrefactor.mode");
        if (mode == null || mode.isBlank()) {
            mode = System.getenv("UTREFACTOR_MODE");
        }
        return "detect".equalsIgnoreCase(mode) || "detection".equalsIgnoreCase(mode);
    }
}
