package Tree;

public class Test {
    public static void main(String[] args) {
        CreateBiTree createBiTree = new CreateBiTree();
        //创建二叉树
        Integer[] arr = {1,2,3,4,5,null,6,7,8,9,10,null,null,11,12};
        for (int i = 0; i < arr.length; i++) {
            System.out.print(arr[i] + " ");
        }
        System.out.println();
        TreeNode root = createBiTree.createTree(arr);

        //层序遍历
        createBiTree.levelOrderTraversal(root);
        System.out.println();

        //分层显示层序遍历
        createBiTree.levelOrderTraversalByTree(root);
    }
}
