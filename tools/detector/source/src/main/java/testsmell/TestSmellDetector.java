package testsmell;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.TypeDeclaration;
import org.apache.commons.lang3.StringUtils;
import testsmell.smell.*;
import thresholds.Thresholds;

import java.io.FileNotFoundException;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Collectors;

public class TestSmellDetector {

    private List<AbstractSmell> testSmells;
    private Thresholds thresholds;

    /**
     * Instantiates the various test smell analyzer classes and loads the objects into an list.
     * Each smell analyzer is initialized with a threshold object to set the most appropriate rule for the detection
     *
     * @param thresholds it could be the default threshold of the ones defined by Spadini
     */
    public TestSmellDetector(Thresholds thresholds) {
        this.thresholds = thresholds;
        initializeSmells();
    }

    private void initializeSmells() {
        testSmells = new ArrayList<>();
        testSmells.add(new AssertionRoulette(thresholds));
        testSmells.add(new ConditionalTestLogic(thresholds));
        testSmells.add(new ConstructorInitialization(thresholds));
        testSmells.add(new DefaultTest(thresholds));
        testSmells.add(new EmptyTest(thresholds));
        testSmells.add(new ExceptionCatchingThrowing(thresholds));
        testSmells.add(new GeneralFixture(thresholds));
        testSmells.add(new MysteryGuest(thresholds));
        testSmells.add(new PrintStatement(thresholds));
        testSmells.add(new RedundantAssertion(thresholds));
        testSmells.add(new SensitiveEquality(thresholds));
        testSmells.add(new VerboseTest(thresholds));
        testSmells.add(new SleepyTest(thresholds));
        testSmells.add(new EagerTest(thresholds));
        testSmells.add(new LazyTest(thresholds));
        testSmells.add(new DuplicateAssert(thresholds));
        testSmells.add(new UnknownTest(thresholds));
        testSmells.add(new IgnoredTest(thresholds));
        testSmells.add(new ResourceOptimism(thresholds));
        testSmells.add(new MagicNumberTest(thresholds));
        testSmells.add(new DependentTest(thresholds));
    }

    public void setTestSmells(List<AbstractSmell> testSmells) {
        this.testSmells = testSmells;
    }

    /**
     * Provides the names of the smells that are being checked for in the code
     *
     * @return list of smell names
     */
    public List<String> getTestSmellNames() {
        return testSmells.stream().map(AbstractSmell::getSmellName).collect(Collectors.toList());
    }

    /**
     * Loads the java source code file into an AST and then analyzes it for the existence of the different types of
     * test smells
     */
    public TestFile detectSmells(TestFile testFile) throws IOException {
        initializeSmells();
        CompilationUnit testFileCompilationUnit = null;
        CompilationUnit productionFileCompilationUnit = null;
        if (!StringUtils.isEmpty(testFile.getTestFilePath())) {
            Path testPath = Paths.get(testFile.getTestFilePath());
            testFileCompilationUnit = JavaParser.parse(testPath, StandardCharsets.UTF_8);
            AssertionUtil.registerSourceRoots(testFileCompilationUnit,
                    sourceRoots(testPath, pathOrNull(testFile.getProductionFilePath())));
            TypeDeclaration typeDeclaration = testFileCompilationUnit.getTypes().get(0);
            testFile.setNumberOfTestMethods(typeDeclaration.getMethods().size());
        }

        if (!StringUtils.isEmpty(testFile.getProductionFilePath())) {
            productionFileCompilationUnit = JavaParser.parse(Paths.get(testFile.getProductionFilePath()), StandardCharsets.UTF_8);
        }

        for (AbstractSmell smell : testSmells) {
            if (productionFileCompilationUnit == null && (smell instanceof EagerTest || smell instanceof LazyTest)) {
                testFile.addSmell(null);
                continue;
            }
            try {
                smell.runAnalysis(testFileCompilationUnit, productionFileCompilationUnit,
                        testFile.getTestFileNameWithoutExtension(),
                        testFile.getProductionFileNameWithoutExtension());
            } catch (RuntimeException | FileNotFoundException e) {
                System.err.println("[rule-failure] test=" + testFile.getTestFilePath()
                        + ", smell=" + smell.getSmellName() + ", reason=" + e.getMessage());
                testFile.addRuleFailure(smell.getSmellName(), e.getClass().getSimpleName()
                        + ": " + String.valueOf(e.getMessage()));
                testFile.addSmell(null);
                continue;
            }
            testFile.addSmell(smell);
        }
        return testFile;
    }

    private static Path pathOrNull(String value) {
        return StringUtils.isEmpty(value) ? null : Paths.get(value);
    }

    private static List<Path> sourceRoots(Path testPath, Path productionPath) {
        List<Path> roots = new ArrayList<>();
        addConventionalRoots(roots, testPath);
        addConventionalRoots(roots, productionPath);
        addWorkspaceDatasetRoots(roots, testPath);
        return roots;
    }

    /**
     * Split inputs can legitimately have no production path. In that case discover conventional
     * dataset source roots from an ancestor workspace, without assuming a project or class name.
     */
    private static void addWorkspaceDatasetRoots(List<Path> roots, Path testPath) {
        if (testPath == null) return;
        Path ancestor = testPath.toAbsolutePath().normalize().getParent();
        while (ancestor != null) {
            Path datasets = ancestor.resolve("datasets");
            if (Files.isDirectory(datasets)) {
                try (java.nio.file.DirectoryStream<Path> projects = Files.newDirectoryStream(datasets)) {
                    for (Path project : projects) {
                        if (Files.isDirectory(project)) addConventionalRoots(roots,
                                project.resolve(Paths.get("src", "test", "java", "placeholder.java")));
                    }
                } catch (IOException ignored) {
                    // An unreadable workspace root supplies no proof; wildcard resolution remains unresolved.
                }
                return;
            }
            ancestor = ancestor.getParent();
        }
    }

    private static void addConventionalRoots(List<Path> roots, Path sourceFile) {
        if (sourceFile == null) return;
        Path absolute = sourceFile.toAbsolutePath().normalize();
        for (int i = 0; i + 2 < absolute.getNameCount(); i++) {
            if (!"src".equals(absolute.getName(i).toString())) continue;
            String kind = absolute.getName(i + 1).toString();
            if (!("main".equals(kind) || "test".equals(kind))
                    || !"java".equals(absolute.getName(i + 2).toString())) continue;
            Path projectRoot = absolute.getRoot().resolve(absolute.subpath(0, i));
            Path main = projectRoot.resolve(Paths.get("src", "main", "java"));
            Path test = projectRoot.resolve(Paths.get("src", "test", "java"));
            if (Files.isDirectory(main) && !roots.contains(main)) roots.add(main);
            if (Files.isDirectory(test) && !roots.contains(test)) roots.add(test);
        }
    }
}
