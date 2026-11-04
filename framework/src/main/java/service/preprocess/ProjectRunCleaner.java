package service.preprocess;

import java.io.File;

public class ProjectRunCleaner {
    public void clean(String projectFilepath) {
        if (projectFilepath == null || projectFilepath.isBlank()) {
            return;
        }
        File projectDir = new File(projectFilepath);
        if (!projectDir.exists() || !projectDir.isDirectory()) {
            return;
        }

        deleteLegacySplitDirectories(projectDir);

        File jsonDir = new File("temp/json/" + projectDir.getName());
        deleteRecursively(jsonDir);

        File printLlmFile = new File("temp/print/" + projectDir.getName() + "_llm.txt");
        deleteRecursively(printLlmFile);
        File printRemoveFile = new File("temp/print/" + projectDir.getName() + "_remove.txt");
        deleteRecursively(printRemoveFile);
    }

    private void deleteLegacySplitDirectories(File directory) {
        if (directory == null || !directory.isDirectory()) {
            return;
        }
        if ("test_split".equals(directory.getName())
                && directory.getParentFile() != null
                && "src".equals(directory.getParentFile().getName())) {
            deleteRecursively(directory);
            return;
        }
        File[] children = directory.listFiles(File::isDirectory);
        if (children == null) {
            return;
        }
        for (File child : children) {
            deleteLegacySplitDirectories(child);
        }
    }

    private void deleteRecursively(File file) {
        if (file == null || !file.exists()) {
            return;
        }
        if (file.isDirectory()) {
            File[] children = file.listFiles();
            if (children != null) {
                for (File child : children) {
                    deleteRecursively(child);
                }
            }
        }
        if (!file.delete()) {
            System.out.println("failed to delete path: " + file.getAbsolutePath());
        }
    }
}
