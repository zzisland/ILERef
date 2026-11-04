package service.preprocess;

import antlr.Java8Parser;
import com.github.javaparser.StaticJavaParser;
import com.google.common.collect.Lists;
import com.google.common.collect.Maps;
import entity.TestCodeContext;
import entity.TestContext;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;
import com.github.javaparser.utils.Log;
import org.antlr.v4.runtime.CharStream;
import org.antlr.v4.runtime.ParserRuleContext;
import org.antlr.v4.runtime.Token;
import org.antlr.v4.runtime.misc.Interval;
import org.apache.commons.lang3.StringUtils;
import util.FileUtil;

import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.nio.file.Path;

public class TestFileSplitService {
    public static final String SKELETON_PLACEHOLDER = "    // UTREFACTOR_TEST_METHODS";

    public void writeSkeletons(List<TestContext> testContexts, String projectName, String skeletonDir) {
        for (TestContext testContext : testContexts) {
            String testFilepath = testContext.getTestFilepath();
            TreeParseService treeParseService = new TreeParseService();
            Java8Parser.CompilationUnitContext parseTree = treeParseService.getParseTree(testFilepath);
            List<ParserRuleContext> testMethodCtxList = treeParseService.getTestMethodCtxList(parseTree);
            String skeleton = generateSkeletonContent(parseTree, testMethodCtxList);
            if (StringUtils.isBlank(skeleton)) {
                Log.info("skip skeleton generation for file without detected test methods: " + testFilepath);
                continue;
            }
            if (!isValidJavaSource(skeleton)) {
                Log.info("skip invalid skeleton for: " + testFilepath);
                continue;
            }
            String skeletonFilepath = skeletonDir + "/" + skeletonRelativePath(testContext);
            FileUtil.writeDataToFile(skeletonFilepath, skeleton);
        }
    }

    public List<TestContext> splitTest(List<TestContext> testContexts, String splitDir) {
        List<TestContext> newContextList = Lists.newArrayList();
        for (TestContext testContext : testContexts) {
            String projectName = testContext.getProjectName();
            String classFilepath = testContext.getClassFilepath();
            String testFilepath = testContext.getTestFilepath();

            TreeParseService treeParseService = new TreeParseService();
            Java8Parser.CompilationUnitContext parseTree = treeParseService.getParseTree(testFilepath);
            List<ParserRuleContext> testMethodCtxList = treeParseService.getTestMethodCtxList(parseTree);

            TestFileSplitService testFileSplitService = new TestFileSplitService();
            List<TestCodeContext> split = testFileSplitService.split(
                    testContext, parseTree, testMethodCtxList, splitDir);

            List<TestContext> splitTestContextList = split.stream().map(x -> TestContext.builder()
                    .projectName(projectName)
                    .modulePath(testContext.getModulePath())
                    .originalTestFilepath(testContext.getOriginalTestFilepath())
                    .testRelativePath(testContext.getTestRelativePath())
                    .testPackageName(testContext.getTestPackageName())
                    .classFilepath(classFilepath)
                    .testFilepath(x.getSplitFilepath())
                    .testOriginalCode(x.getOriginalCode())
                    .methodCtx(x.getMethodCtx())
                    .testCodeStartLine(x.getTestCodeStartLine())
                    .testCodeEndLine(x.getTestCodeEndLine())
                    .splitFileName(x.getSplitFileName())
                    .build()).toList();
            newContextList.addAll(splitTestContextList);
        }

        return newContextList;
    }

    public List<TestCodeContext> split(TestContext testContext, Java8Parser.CompilationUnitContext parseTree,
                                       List<ParserRuleContext> testMethodCtxList, String splitDir) {
        List<TestCodeContext> testCodeContextList = Lists.newArrayList();

        if (StringUtils.isBlank(splitDir)) {
            throw new IllegalArgumentException("splitDir must point to the current run directory");
        }

        int index = 0;
        for (ParserRuleContext methodCtx : testMethodCtxList) {
            String testOriginalCode = getOriginalCode(methodCtx.getStart(), methodCtx.getStart().getStartIndex(), methodCtx.getStop().getStopIndex());

            index++;
            TestSplitVisitor testSplitVisitor = new TestSplitVisitor(methodCtx.getStart(),
                    methodCtx.getStart().getStartIndex(),
                    methodCtx.getStop().getStopIndex());
            testSplitVisitor.visit(parseTree);

            testSplitVisitor.getRemoveIndexList()
                    .sort(Comparator.comparingInt(TestSplitVisitor.RemoveIndex::getStartIndex));

            // total: 1-20
            // keep:  4-6
            // remove: 8-10, 12-15, 17-18
            String newFileContent = StringUtils.EMPTY;
            try {
                newFileContent = generateNewFileContent(parseTree, testSplitVisitor);
            } catch (Exception e) {
                Log.info("generate FileContent error: " + e.getMessage());
            }

            String newFilepath = Path.of(splitDir, splitRelativePath(testContext, index)).toString();
            if (StringUtils.isBlank(newFileContent)) {
                Log.info("newFilepath content is blank: " + newFilepath);
                continue;
            }
            if (!isValidJavaSource(newFileContent)) {
                Log.info("skip invalid split java source: " + newFilepath);
                continue;
            }
            FileUtil.writeToNewFile(newFileContent, newFilepath);

            TestCodeContext testCodeContext = TestCodeContext.builder()
                    .originalCode(testOriginalCode)
                    .methodCtx(methodCtx)
                    .splitFilepath(newFilepath)
                    .splitFileName(FileUtil.getNameByFilepath(newFilepath))
                    .testCodeStartLine(methodCtx.getStart().getLine())
                    .testCodeEndLine(methodCtx.getStop().getLine())
                    .build();
            testCodeContextList.add(testCodeContext);

        }
        return testCodeContextList;
    }

    private static String generateNewFileContent(Java8Parser.CompilationUnitContext parseTree, TestSplitVisitor testSplitVisitor) {
        StringBuilder splitCodeStr = new StringBuilder();
        int currentStartIndex = parseTree.getStart().getStartIndex();
        int fileStopIndex = parseTree.getStop().getStopIndex();

        for (TestSplitVisitor.RemoveIndex removeIndex : testSplitVisitor.getRemoveIndexList()) {
            if (currentStartIndex <= removeIndex.getStartIndex() - 1) {
                splitCodeStr.append(
                        getOriginalCode(parseTree.getStart(), currentStartIndex, removeIndex.getStartIndex() - 1)
                );
            }
            currentStartIndex = removeIndex.getEndIndex() + 1;
        }

        if (currentStartIndex <= fileStopIndex) {
            splitCodeStr.append(
                    getOriginalCode(parseTree.getStart(), currentStartIndex, fileStopIndex)
            );
        }
        return splitCodeStr.toString();
    }

    private static String generateSkeletonContent(Java8Parser.CompilationUnitContext parseTree,
                                                  List<ParserRuleContext> testMethodCtxList) {
        if (testMethodCtxList == null || testMethodCtxList.isEmpty()) {
            return StringUtils.EMPTY;
        }

        ParserRuleContext primaryClassCtx = findPrimaryTestClass(testMethodCtxList.get(0));
        if (primaryClassCtx == null || primaryClassCtx.getStop() == null) {
            return StringUtils.EMPTY;
        }

        int classStopIndex = primaryClassCtx.getStop().getStopIndex();
        int fileStopIndex = parseTree.getStop().getStopIndex();
        StringBuilder classSkeleton = new StringBuilder();
        int currentStartIndex = parseTree.getStart().getStartIndex();
        List<ParserRuleContext> sortedMethods = testMethodCtxList.stream()
                .sorted(Comparator.comparingInt(ctx -> ctx.getStart().getStartIndex()))
                .toList();

        for (ParserRuleContext methodCtx : sortedMethods) {
            int methodStartIndex = methodCtx.getStart().getStartIndex();
            if (methodStartIndex > classStopIndex) {
                break;
            }
            if (currentStartIndex < methodStartIndex) {
                classSkeleton.append(getOriginalCode(parseTree.getStart(), currentStartIndex, methodStartIndex - 1));
            }
            currentStartIndex = methodCtx.getStop().getStopIndex() + 1;
        }
        if (currentStartIndex <= classStopIndex) {
            classSkeleton.append(getOriginalCode(parseTree.getStart(), currentStartIndex, classStopIndex));
        }

        int classClosingBrace = classSkeleton.lastIndexOf("}");
        if (classClosingBrace < 0) {
            return StringUtils.EMPTY;
        }
        classSkeleton.insert(classClosingBrace, System.lineSeparator() + SKELETON_PLACEHOLDER + System.lineSeparator());

        String suffix = StringUtils.EMPTY;
        if (classStopIndex < fileStopIndex) {
            suffix = getOriginalCode(parseTree.getStart(), classStopIndex + 1, fileStopIndex);
        }

        return classSkeleton.append(suffix).toString();
    }

    private static ParserRuleContext findPrimaryTestClass(ParserRuleContext methodCtx) {
        ParserRuleContext current = methodCtx;
        while (current != null) {
            int ruleIndex = current.getRuleIndex();
            if (ruleIndex == Java8Parser.RULE_classDeclaration
                    || ruleIndex == Java8Parser.RULE_normalClassDeclaration) {
                return current;
            }
            if (!(current.getParent() instanceof ParserRuleContext)) {
                break;
            }
            current = (ParserRuleContext) current.getParent();
        }
        return null;
    }

    private static boolean isValidJavaSource(String source) {
        try {
            StaticJavaParser.parse(source);
            return true;
        } catch (RuntimeException e) {
            return false;
        }
    }

    private static String moduleSubdir(String modulePath) {
        if (StringUtils.isBlank(modulePath)) {
            return StringUtils.EMPTY;
        }
        return modulePath.replace("\\", "/") + "/";
    }

    private static String skeletonRelativePath(TestContext testContext) {
        String relativePath = testContext.getTestRelativePath();
        if (StringUtils.isBlank(relativePath)) {
            return moduleSubdir(testContext.getModulePath())
                    + FileUtil.getFileName(testContext.getTestFilepath());
        }
        String normalized = relativePath.replace("\\", "/");
        String rootMarker = "src/test/java/";
        if (normalized.startsWith(rootMarker)) {
            return normalized.substring(rootMarker.length());
        }
        return normalized.replace("/" + rootMarker, "/");
    }

    static String splitRelativePath(TestContext testContext, int index) {
        String relativePath = skeletonRelativePath(testContext).replace("\\", "/");
        int extensionIndex = relativePath.lastIndexOf(".java");
        String stem = extensionIndex >= 0 ? relativePath.substring(0, extensionIndex) : relativePath;
        return stem + "__" + index + ".java";
    }

    private static String getOriginalCode(Token startToken, int startIndex, int stopIndex) {
        // Get the input stream
        CharStream inputStream = startToken.getInputStream();

        // Extract the original source code
        return inputStream.getText(new Interval(startIndex, stopIndex));
    }
}


